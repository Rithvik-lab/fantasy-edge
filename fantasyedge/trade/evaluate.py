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

import zlib
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from fantasyedge.league import LeagueSettings
from fantasyedge.models.season_sim import (
    DEFAULT_WEEKLY_CV,
    GAMES_DISPERSION,
    MAX_GAMES,
    _per_game_sampler,
)

N_SIMS = 4000


# A season is seventeen weeks. Dividing by it turns a season total into the
# unit managers actually reason in, which is what they see on a Sunday.
SEASON_WEEKS = 17


def magnitudes(delta: float, before_median: float) -> tuple[float, float]:
    """(percent change, points per week) for a season-points delta.

    THE ONLY TWO PERCENTAGES HERE THAT MEAN ANYTHING, and they are deliberately
    modest:

      PERCENT CHANGE is the delta over the season total you had. It is real
      division, not a score -- +31 on a 1,950-point season is +1.6%, and that
      smallness is the honest reading rather than a failure to find a bigger
      number.

      PER WEEK is the same delta spread across the remaining season, because
      "+31 points" is hard to feel and "+1.8 a week" is not. A weekly matchup
      is usually decided by twenty or thirty, so this is the line that tells
      you whether a trade moves a single game.

    There is deliberately NO "you won 68% of this trade". That number is not
    defined -- there is no denominator that survives contact with a package of
    unequal size -- and inventing one would be the most quotable thing on the
    screen and also the only made-up thing on it.
    """
    pct = (delta / before_median * 100.0) if before_median > 0 else 0.0
    return pct, delta / SEASON_WEEKS


def _seed_for(player_id: str) -> int:
    """Stable per-player seed, so a player draws the same season on both sides.

    STABLE ACROSS PROCESSES, not merely within one. `hash()` on a string is
    salted per interpreter, so every restart of the engine dealt every player a
    different season and the same trade came back with a different verdict --
    Jeremiyah Love for Chase Brown priced at -0.5, -3.6 and -8.4 points on
    three consecutive runs of identical code. Common random numbers held inside
    a single answer, which hid it: both sides moved together, so the arithmetic
    was always self-consistent and never twice the same.

    crc32 is not a hash function anyone should use for security. It is exactly
    right here: fixed by the standard, identical everywhere, and fast.
    """
    return zlib.crc32(f"fantasyedge-trade/{player_id}".encode()) % (2**31 - 1)


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
    roster: pl.DataFrame, settings: LeagueSettings, n_sims: int = N_SIMS,
    waiver: dict[str, float] | None = None,
) -> np.ndarray:
    """Season totals of the best lineup this roster can field, per simulation.

    Vectorised by position. Within a position the weekly scores are sorted
    descending and the top `count` start.

    `waiver` is what a free agent at each position scores in a week, and it is
    the FLOOR on every slot. Without it a week where your only tight end is hurt
    scores zero at tight end, which nobody has ever let happen -- you add
    somebody on Wednesday. Pricing that week at nothing made a backup worth the
    whole slot rather than the difference between him and the wire, and the
    scan noticed: swapping a bench back for a bench tight end came back +32
    with not one starting slot changing hands.

    Same error the roster-level fix caught, one level down -- there it was an
    empty slot for the season, here it is an empty slot for a week.
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
    w = waiver or {}

    for slot, count in settings.lineup.items():
        if slot in ("FLEX", "SUPERFLEX"):
            continue
        floor = float(w.get(slot, 0.0))
        arr = stacked.get(slot)
        if arr is None:
            # Nobody at all here: the slot is the wire, every week.
            total += floor * count
            continue
        got = arr[:, :, :count]
        if got.shape[-1] < count:
            total += floor * (count - got.shape[-1])
        total += np.maximum(got, floor).sum(axis=-1)
        if slot in settings.flex_eligible and arr.shape[-1] > count:
            leftovers.append(arr[:, :, count:])

    flex = settings.lineup.get("FLEX", 0)
    if flex:
        floor = max((float(w.get(p, 0.0)) for p in settings.flex_eligible),
                    default=0.0)
        if leftovers:
            pool = np.concatenate(leftovers, axis=-1)
            pool = np.sort(pool, axis=-1)[:, :, ::-1]
            got = pool[:, :, :flex]
            if got.shape[-1] < flex:
                total += floor * (flex - got.shape[-1])
            total += np.maximum(got, floor).sum(axis=-1)
        else:
            total += floor * flex

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
    # False when there was no roster to price against. The difference matters
    # enough that it travels with the verdict rather than being inferred.
    roster_priced: bool = True
    overlap: dict = field(default_factory=dict)
    # Size of the move, expressed three ways because one number cannot carry
    # it. See `magnitudes` for what each is and is not.
    pct_change: float = 0.0
    per_week: float = 0.0
    roster_before: int = 0
    roster_after: int = 0
    # Slots this trade leaves you filling off waivers, and by whom. Empty for
    # a trade that leaves your lineup whole.
    streamed: list[dict] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "delta_median": round(self.delta_median, 1),
            # THE MEAN AND THE MEDIAN DISAGREE WHENEVER A TAIL IS INVOLVED, and
            # a tail is exactly where an unproven player differs from a proven
            # one -- `models/rookie_risk` measured the whole rookie penalty as
            # a left-tail effect and nothing else. A verdict quoting only the
            # median is structurally blind to it, which is how a rookie for a
            # veteran at the same projection reads as a coin flip.
            "delta_mean": round(self.after.get("mean", 0.0)
                                - self.before.get("mean", 0.0), 1),
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
            "roster_priced": self.roster_priced,
            "overlap": self.overlap,
            "pct_change": round(self.pct_change, 2),
            "per_week": round(self.per_week, 2),
            "roster_before": self.roster_before,
            "roster_after": self.roster_after,
            "streamed": [{k: v for k, v in f.items() if k != "row"}
                         for f in self.streamed],
            "note": self.note,
        }


def _waiver_fill(roster: pl.DataFrame, settings: LeagueSettings,
                 pool: pl.DataFrame | None) -> list[dict]:
    """The free agents you would be starting, for slots this roster cannot fill.

    AN EMPTY SLOT IS NOT WORTH ZERO. Nobody plays a week with an empty
    quarterback slot -- they add the best one left on the wire, and how much
    that costs depends entirely on the position. In a 12-team league starting
    one quarterback, the best free agent is roughly the 13th best quarterback
    in football and barely worse than the 12th. At running back the wire is
    picked clean by September and the drop is enormous.

    So the cost of emptying a slot is measured rather than assumed: the best
    unowned man at that position, from the same board everything else is priced
    on. No list of "streamable positions" is needed and none is used -- the
    pool says which is which, in this league, at this moment.
    """
    if pool is None or not pool.height or not roster.height:
        return []
    have: dict[str, int] = {}
    for pos in roster["position"].to_list():
        have[pos] = have.get(pos, 0) + 1
    fills = []
    for slot, count in settings.lineup.items():
        if slot in ("FLEX", "SUPERFLEX"):
            continue
        short = count - have.get(slot, 0)
        if short <= 0:
            continue
        best = (pool.filter(pl.col("position") == slot)
                    .sort("projected_points", descending=True, nulls_last=True)
                    .head(short))
        for r in best.iter_rows(named=True):
            fills.append({
                "slot": slot,
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "position": r["position"],
                "points": round(float(r.get("projected_points") or 0.0), 1),
                "row": r,
            })
    return fills


def _waiver_rate(pool: pl.DataFrame | None) -> dict[str, float]:
    """Weekly points from the best free agent at each position.

    The floor under every slot in every simulated week. Nobody plays a week
    with an empty slot -- they add somebody on Wednesday -- so a backup is
    worth the difference between himself and the wire, not the whole slot.
    """
    if pool is None or not pool.height:
        return {}
    out: dict[str, float] = {}
    best = (pool.with_columns(
                (pl.col("projected_points")
                 / pl.col("expected_games").clip(1.0, None)).alias("_wk"))
                .group_by("position").agg(pl.col("_wk").max()))
    for r in best.iter_rows(named=True):
        if r["position"] and r["_wk"] is not None:
            out[r["position"]] = float(r["_wk"])
    return out


def _with(roster: pl.DataFrame, fills: list[dict]) -> pl.DataFrame:
    """The roster plus the men you would have to add to field a lineup."""
    if not fills:
        return roster
    add = pl.DataFrame([f["row"] for f in fills])
    cols = [c for c in roster.columns if c in add.columns]
    return pl.concat([roster.select(cols), add.select(cols)], how="vertical")


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
    free_agents: pl.DataFrame | None = None,
) -> TradeVerdict:
    """Price a trade by what it does to your startable season.

    `roster` is your team now; `give_ids` leave it and `get_ids` join it. The
    verdict is the change in simulated starting-lineup points, plus the naive
    value total beside it so the gap between the two is visible -- that gap IS
    the opportunity cost, and it is the number that makes a three-for-one look
    fair right up until you have to bench two of them.

    `free_agents` is everyone nobody owns. Without it an emptied slot scores
    ZERO, which is wrong and wrong by a lot: trading your only quarterback does
    not mean starting nobody, it means starting whoever is on waivers, and at
    quarterback that man is nearly as good as the one you sent. The same trade
    at running back really is close to catastrophic, because the waiver wire
    there is empty. That difference is the whole point and it falls out of the
    pool rather than out of a rule about which positions are streamable.
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
    before_fill = _waiver_fill(roster, settings, free_agents)
    after_fill = _waiver_fill(after, settings, free_agents)
    rate = _waiver_rate(free_agents)

    before_totals = simulate_lineup(_with(roster, before_fill), settings,
                                    n_sims, waiver=rate)
    after_totals = simulate_lineup(_with(after, after_fill), settings,
                                   n_sims, waiver=rate)

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

    # Slots this trade leaves you filling off the wire, and by whom. The
    # difference between the two lists is the honest version of "you would have
    # nobody there" -- it names the man you would actually start.
    had = {f["slot"] for f in before_fill}
    streamed = [f for f in after_fill if f["slot"] not in had]

    return TradeVerdict(
        overlap=distribution_overlap(before_totals, after_totals),
        pct_change=magnitudes(real, b["median"])[0],
        per_week=magnitudes(real, b["median"])[1],
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
        streamed=streamed,
        note=note,
    )


def for_everyone(
    rosters: dict[int, pl.DataFrame],
    give_ids: list[str],
    get_ids: list[str],
    settings: LeagueSettings,
    board: pl.DataFrame,
    n_sims: int = 1500,
    free_agents: pl.DataFrame | None = None,
) -> dict:
    """What this same swap would be worth to every other team in the league.

    TWO NUMBERS, AND THE GAP BETWEEN THEM IS THE POINT.

    "Is this a good trade" and "is this a good trade FOR ME" are different
    questions and the difference is the entire reason this app exists. A deal
    that is worth +12 to a typical roster and +70 to yours is not a steal, it
    is a FIT -- you happen to be thin exactly where it helps. The reverse is
    the one that costs people leagues: a package everyone else would love,
    landing on the one roster that cannot use it.

    Both numbers come from the same simulation. Each opponent is handed the men
    you would send -- they have to hold them before they can trade them -- and
    then makes the swap on their own roster, with their own holes, their own
    depth and the same waiver wire. The median across the league is what the
    deal is worth in general; yours is what it is worth to you.

    Teams already holding somebody on the incoming side are skipped: you cannot
    trade for a player you own, and pretending otherwise doubles him up in the
    lineup.
    """
    incoming = set(get_ids)
    deltas: list[tuple[int, float]] = []
    for tid, roster in rosters.items():
        if not roster.height or incoming & set(roster["player_id"].to_list()):
            continue
        held = board.filter(pl.col("player_id").is_in(give_ids))
        cols = [c for c in roster.columns if c in held.columns]
        theirs = pl.concat([roster.select(cols), held.select(cols)],
                           how="vertical") if held.height else roster
        v = evaluate(theirs, give_ids, get_ids, settings, board,
                     n_sims=n_sims, free_agents=free_agents)
        deltas.append((int(tid), round(v.delta_median, 1)))

    if not deltas:
        return {}
    vals = sorted(d for _, d in deltas)
    mid = vals[len(vals) // 2] if len(vals) % 2 else \
        (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    return {
        "median": round(float(mid), 1),
        "low": vals[0],
        "high": vals[-1],
        "teams": len(vals),
        "by_team": dict(deltas),
    }


def compare_packages(
    board: pl.DataFrame,
    give_ids: list[str],
    get_ids: list[str],
    n_sims: int = N_SIMS,
) -> TradeVerdict:
    """Two piles of players, with no roster behind them.

    FOR SPECULATION, AND HONEST ABOUT WHAT IT CANNOT SEE.

    "What if I got this guy and sent him that guy" is a real question people
    ask before they own anybody -- about a player they are chasing, or a deal
    two moves away. Refusing to answer it because no roster is synced is the
    tool being precious.

    But this is NOT the lineup verdict and must never be shown as one. Without
    a roster there are no starting slots, so the one thing this engine exists
    to price -- what the bodies cost you -- is invisible here. It compares the
    two sides on their own season distributions and says so. `roster_priced`
    is False on the way out, and the interface has to say that out loud.
    """
    give = board.filter(pl.col("player_id").is_in(give_ids))
    get = board.filter(pl.col("player_id").is_in(get_ids))

    def totals(df: pl.DataFrame) -> np.ndarray:
        if not df.height:
            return np.zeros(n_sims)
        out = np.zeros(n_sims)
        for row in df.iter_rows(named=True):
            out += _player_weeks(row, n_sims).sum(axis=1)
        return out

    # Common random numbers again: anyone appearing on both sides draws the
    # same season, so the difference is the swap rather than sampling noise.
    a, b = totals(give), totals(get)
    sa, sb = _summary(a), _summary(b)

    give_rows, get_rows = _rows(board, give_ids), _rows(board, get_ids)
    naive = (sum(r.get("vor") or 0.0 for r in get_rows)
             - sum(r.get("vor") or 0.0 for r in give_rows))

    return TradeVerdict(
        overlap=distribution_overlap(a, b),
        pct_change=magnitudes(sb["median"] - sa["median"], sa["median"])[0],
        per_week=magnitudes(sb["median"] - sa["median"], sa["median"])[1],
        delta_median=sb["median"] - sa["median"],
        delta_floor=sb["floor"] - sa["floor"],
        delta_ceiling=sb["ceiling"] - sa["ceiling"],
        win_probability=float((b > a).mean()),
        before=sa,
        after=sb,
        give=give_rows,
        get=get_rows,
        naive_value_delta=naive,
        opportunity_gap=0.0,
        roster_before=0,
        roster_after=0,
        roster_priced=False,
        note=("No roster, so this compares the two sides on their own season "
              "output only. It cannot price what the extra bodies cost you — "
              "add your players on the left for that."),
    )


def distribution_overlap(before: np.ndarray, after: np.ndarray,
                         bins: int = 120) -> dict:
    """How much the two seasons are the SAME season.

    The overlapping coefficient: the area under min(f, g) once both are
    normalised. Integrated numerically over a shared grid, because these are
    empirical distributions from the simulation rather than anything with a
    closed form -- and using the real samples means skew and fat tails are
    included instead of assumed away.

        1.0   the two teams are indistinguishable
        0.0   they never produce the same season

    This is the number that should temper a headline. A trade can read "+31
    points" and still overlap 0.86, which means that in most seasons you could
    not tell which side you took. Reporting the gain without it is how a small
    edge gets sold as a certainty.
    """
    lo = float(min(before.min(), after.min()))
    hi = float(max(before.max(), after.max()))
    if hi <= lo:
        return {"overlap": 1.0, "lo": lo, "hi": hi}

    edges = np.linspace(lo, hi, bins + 1)
    width = edges[1] - edges[0]
    fb, _ = np.histogram(before, bins=edges, density=True)
    fa, _ = np.histogram(after, bins=edges, density=True)
    coeff = float(np.minimum(fb, fa).sum() * width)

    # Also hand back the cumulative curves, so the interface can answer "how
    # often is the season below X" at any point the pointer lands on.
    centres = (edges[:-1] + edges[1:]) / 2
    cb = np.searchsorted(np.sort(before), centres) / len(before)
    ca = np.searchsorted(np.sort(after), centres) / len(after)

    return {
        "overlap": round(min(max(coeff, 0.0), 1.0), 3),
        "lo": round(lo, 1),
        "hi": round(hi, 1),
        "grid": [round(float(x), 1) for x in centres],
        "cdf_before": [round(float(x), 4) for x in cb],
        "cdf_after": [round(float(x), 4) for x in ca],
    }
