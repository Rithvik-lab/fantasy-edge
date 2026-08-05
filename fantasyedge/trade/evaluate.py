"""What a trade does to the lineup you can actually field.

THE THING EVERY TRADE CALCULATOR GETS WRONG

Add up value on both sides and compare. On that scale, McCaffrey for three
mid-tier starters is a clear win -- three positive numbers beat one. In a real
league it is usually a loss, because two of those three go straight to your
bench, and a bench player is not worth his projection. He is worth the small
amount he beats your next-best option by, times the odds you ever need him.

Roster spots are the scarce resource and value totals cannot see them.

WHAT THIS DOES INSTEAD

Simulates the season week by week and asks one question: how many points does
my BEST LEGAL STARTING LINEUP score, before the trade and after it? Everything
falls out of that:

  - A fourth running back who never cracks the lineup adds nothing, correctly,
    without any rule saying so.
  - Depth is priced honestly rather than at zero, because starters miss games
    and somebody has to fill the slot that week.
  - Going over the roster limit forces cuts, and the cuts are part of the
    price. This is the "you do not have those three extra spots" problem,
    priced rather than described.

WHY WEEK BY WEEK

Season totals cannot see a bye week or an injury. Two rosters with identical
season projections are different teams if one has a backup at the position
where its starter misses six games. The lineup is refilled every simulated
week from whoever is available, which is the only way that difference shows up.

COMMON RANDOM NUMBERS

Both sides of the comparison are simulated with the same draws for every
player who appears in both -- the seed comes from the player id, not from a
counter. So the difference between before and after is the trade, not
sampling noise. Without this, a 5-point edge is indistinguishable from jitter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from fantasyedge import config
from fantasyedge.league import LeagueSettings
from fantasyedge.models.season_sim import (
    DEFAULT_WEEKLY_CV,
    GAMES_DISPERSION,
    MAX_GAMES,
    _per_game_sampler,
)

N_SIMS = 4000


def _seed_for(player_id: str) -> int:
    """Stable per-player seed, so a player draws the same season on both sides."""
    return abs(hash(("fantasyedge-trade", player_id))) % (2**31 - 1)


def _player_weeks(row: dict, n_sims: int) -> np.ndarray:
    """(n_sims, 17) of weekly points, zero on weeks he does not play.

    Which weeks are missed is RANDOMISED rather than always the last few. That
    detail matters more than it looks: if every player misses the same trailing
    weeks, absences line up perfectly across a roster and depth never gets to
    cover anything, so the simulation quietly prices every bench player at zero.
    """
    rng = np.random.default_rng(_seed_for(row["player_id"]))

    q20 = row.get("season_p20")
    q50 = row.get("season_p50")
    q80 = row.get("season_p80")
    games = row.get("expected_games") or MAX_GAMES
    if q50 is None:
        # No simulated band: fall back to a flat rate off the projection.
        pts = float(row.get("projected_points") or 0.0)
        q20 = q50 = q80 = pts
        per_game = pts / max(games, 1.0)
        rate = np.full(n_sims, per_game)
    else:
        # The board carries SEASON quantiles; convert to a per-game rate so
        # games played and scoring rate stay separable.
        g = max(float(games), 1.0)
        rate = _per_game_sampler(
            float(q20) / g, float(q50) / g, float(q80) / g, rng, n_sims)

    mean_frac = np.clip(float(games) / MAX_GAMES, 0.02, 0.98)
    a = mean_frac * GAMES_DISPERSION
    b = (1 - mean_frac) * GAMES_DISPERSION
    played = np.rint(rng.beta(a, b, n_sims) * MAX_GAMES).astype(int)

    sd = max(float(rate.mean()), 1.0) * DEFAULT_WEEKLY_CV
    weekly = np.clip(rng.normal(rate[:, None], sd, size=(n_sims, MAX_GAMES)), 0.0, None)

    # A random subset of weeks, of the right size, per simulated season.
    order = rng.random((n_sims, MAX_GAMES)).argsort(axis=1)
    return np.where(order < played[:, None], weekly, 0.0)


def simulate_lineup(
    roster: pl.DataFrame, settings: LeagueSettings, n_sims: int = N_SIMS
) -> np.ndarray:
    """Season totals of the best lineup this roster can field, per simulation.

    Vectorised by position. Within a position the weekly scores are sorted
    descending and the top `count` start; an unavailable player scores zero and
    therefore sorts to the bottom, so he only ever occupies a slot when nobody
    else is left -- in which case the slot is worth zero anyway, which is right.
    """
    if not roster.height:
        return np.zeros(n_sims)

    by_pos: dict[str, list[np.ndarray]] = {}
    for row in roster.iter_rows(named=True):
        by_pos.setdefault(row["position"], []).append(_player_weeks(row, n_sims))

    stacked = {p: np.sort(np.stack(v, axis=-1), axis=-1)[:, :, ::-1]
               for p, v in by_pos.items()}   # (n_sims, weeks, players) desc

    total = np.zeros((n_sims, MAX_GAMES))
    leftovers = []

    for slot, count in settings.lineup.items():
        if slot in ("FLEX", "SUPERFLEX"):
            continue
        arr = stacked.get(slot)
        if arr is None:
            continue
        total += arr[:, :, :count].sum(axis=-1)
        if slot in settings.flex_eligible and arr.shape[-1] > count:
            leftovers.append(arr[:, :, count:])

    flex = settings.lineup.get("FLEX", 0)
    if flex and leftovers:
        pool = np.concatenate(leftovers, axis=-1)
        pool = np.sort(pool, axis=-1)[:, :, ::-1]
        total += pool[:, :, :flex].sum(axis=-1)

    return total.sum(axis=1)


def _trim_to_limit(
    roster: pl.DataFrame, settings: LeagueSettings
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Cut back to the roster limit, worst first.

    THIS IS THE OPPORTUNITY COST, made concrete. Taking three players for one
    means dropping two, and what you drop is part of what you paid. Sorting by
    projection rather than by lineup impact is deliberate: it is what a manager
    actually does at the waiver wire, and modelling a smarter cut would flatter
    the trade.
    """
    limit = settings.roster_size
    if roster.height <= limit:
        return roster, roster.head(0)
    ordered = roster.sort("projected_points", descending=True, nulls_last=True)
    return ordered.head(limit), ordered.tail(roster.height - limit)


@dataclass
class TradeVerdict:
    """Everything needed to explain a trade, in the order it should be read."""

    delta_median: float
    delta_floor: float
    delta_ceiling: float
    win_probability: float
    before: dict[str, float]
    after: dict[str, float]
    give: list[dict] = field(default_factory=list)
    get: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    naive_value_delta: float = 0.0
    opportunity_gap: float = 0.0
    roster_before: int = 0
    roster_after: int = 0
    slots_changed: list[dict] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "delta_median": round(self.delta_median, 1),
            "delta_floor": round(self.delta_floor, 1),
            "delta_ceiling": round(self.delta_ceiling, 1),
            "win_probability": round(self.win_probability, 3),
            "before": {k: round(v, 1) for k, v in self.before.items()},
            "after": {k: round(v, 1) for k, v in self.after.items()},
            "give": self.give,
            "get": self.get,
            "dropped": self.dropped,
            "naive_value_delta": round(self.naive_value_delta, 1),
            "opportunity_gap": round(self.opportunity_gap, 1),
            "roster_before": self.roster_before,
            "roster_after": self.roster_after,
            "slots_changed": self.slots_changed,
            "note": self.note,
        }


def _summary(totals: np.ndarray) -> dict[str, float]:
    return {
        "floor": float(np.quantile(totals, 0.20)),
        "median": float(np.quantile(totals, 0.50)),
        "ceiling": float(np.quantile(totals, 0.80)),
        "mean": float(totals.mean()),
    }


def _rows(board: pl.DataFrame, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    sub = board.filter(pl.col("player_id").is_in(ids))
    keep = [c for c in ("player_id", "player_name", "position", "projected_points",
                        "vor", "season_p20", "season_p50", "season_p80",
                        "expected_games") if c in sub.columns]
    return sub.select(keep).to_dicts()


def evaluate(
    roster: pl.DataFrame,
    give_ids: list[str],
    get_ids: list[str],
    settings: LeagueSettings,
    board: pl.DataFrame,
    n_sims: int = N_SIMS,
) -> TradeVerdict:
    """Price a trade by what it does to your startable season.

    `roster` is your team now; `give_ids` leave it and `get_ids` join it. The
    verdict is the change in simulated starting-lineup points, plus the naive
    value total beside it so the gap between the two is visible -- that gap IS
    the opportunity cost, and it is the number that makes a three-for-one look
    fair right up until you have to bench two of them.
    """
    give = set(give_ids)
    kept = roster.filter(~pl.col("player_id").is_in(list(give)))
    incoming = board.filter(pl.col("player_id").is_in(get_ids))

    if incoming.height:
        cols = [c for c in kept.columns if c in incoming.columns]
        after_full = pl.concat([kept.select(cols), incoming.select(cols)], how="vertical")
    else:
        after_full = kept

    after, dropped = _trim_to_limit(after_full, settings)

    before_totals = simulate_lineup(roster, settings, n_sims)
    after_totals = simulate_lineup(after, settings, n_sims)

    b, a = _summary(before_totals), _summary(after_totals)

    give_rows = _rows(board, give_ids)
    get_rows = _rows(board, get_ids)
    naive = (sum(r.get("vor") or 0.0 for r in get_rows)
             - sum(r.get("vor") or 0.0 for r in give_rows))
    real = a["median"] - b["median"]

    note = ""
    if dropped.height:
        names = ", ".join(dropped["player_name"].to_list())
        note = (f"Over the {settings.roster_size}-man limit, so this trade also "
                f"costs you {names}. That is part of the price.")

    return TradeVerdict(
        delta_median=real,
        delta_floor=a["floor"] - b["floor"],
        delta_ceiling=a["ceiling"] - b["ceiling"],
        # Paired: same player draws on both sides, so this is the probability
        # the trade helps, not the probability one roster beats another.
        win_probability=float((after_totals > before_totals).mean()),
        before=b,
        after=a,
        give=give_rows,
        get=get_rows,
        dropped=dropped.select(
            [c for c in ("player_id", "player_name", "position", "projected_points")
             if c in dropped.columns]).to_dicts() if dropped.height else [],
        naive_value_delta=naive,
        opportunity_gap=naive - real,
        roster_before=roster.height,
        roster_after=after.height,
        note=note,
    )
