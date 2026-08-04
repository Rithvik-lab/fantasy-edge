"""Stateful draft session — configure once, then pick by pick.

The one-shot CLI made you retype every drafted player on every invocation.
This holds state instead: you configure a league, record picks as they
happen, and ask for suggestions that account for everything already taken and
everything you already own.

The command surface is deliberately small and maps one-to-one onto MCP tools
later, so the interface layer is a wrapper rather than a rewrite:

    start    configure the league and your slot
    suggest  best available, given board and roster
    take     you drafted someone
    pick     someone else drafted someone
    roster   your team and what it grades out to
    undo     take back the last pick

State lives in one JSON file so a crashed terminal costs nothing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

import polars as pl

from fantasyedge import config
from fantasyedge.league import LeagueSettings, overall_pick, picks_for_slot, slot_for_pick

SESSION_PATH = config.PROCESSED / "draft_session.json"


@dataclass
class Pick:
    overall: int
    player_id: str
    player_name: str
    position: str
    mine: bool


@dataclass
class Session:
    """Everything about a draft in progress."""

    n_teams: int = 12
    my_slot: int = 1
    points_per_reception: float = 1.0
    roster_size: int = 16
    lineup: dict[str, int] = field(default_factory=lambda: {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1})
    # Starters and bench get separate risk settings. Both accept
    # safe / combined / aggressive.
    risk_tolerance: str = "combined"        # starters
    bench_tolerance: str = "aggressive"     # bench: convex payoff, see engine
    picks: list[dict] = field(default_factory=list)
    created: str = ""

    # -- derived ----------------------------------------------------------

    @property
    def settings(self) -> LeagueSettings:
        return LeagueSettings(
            n_teams=self.n_teams,
            lineup=dict(self.lineup),
            points_per_reception=self.points_per_reception,
            roster_size=self.roster_size,
        )

    @property
    def drafted_ids(self) -> list[str]:
        return [p["player_id"] for p in self.picks]

    @property
    def my_ids(self) -> list[str]:
        return [p["player_id"] for p in self.picks if p["mine"]]

    def on_the_clock(self) -> tuple[int, int, int]:
        """(round, pick_in_round, overall) for the next selection."""
        nxt = len(self.picks) + 1
        rnd = (nxt - 1) // self.n_teams + 1
        pick = (nxt - 1) % self.n_teams + 1
        return rnd, pick, nxt

    def is_my_turn(self) -> bool:
        rnd, pick, _ = self.on_the_clock()
        return slot_for_pick(rnd, pick, self.n_teams) == self.my_slot

    def picks_until_mine(self) -> int | None:
        """How many selections happen before my next one."""
        mine = picks_for_slot(self.my_slot, self.n_teams, self.roster_size)
        nxt = len(self.picks) + 1
        later = [p for p in mine if p >= nxt]
        return (later[0] - nxt) if later else None

    def add(self, player_id: str, name: str, position: str, mine: bool) -> Pick:
        _, _, overall = self.on_the_clock()
        p = Pick(overall, player_id, name, position, mine)
        self.picks.append(asdict(p))
        return p

    def undo(self) -> dict | None:
        return self.picks.pop() if self.picks else None

    # -- persistence ------------------------------------------------------

    def save(self) -> None:
        config.PROCESSED.mkdir(parents=True, exist_ok=True)
        SESSION_PATH.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls) -> "Session":
        if not SESSION_PATH.exists():
            raise FileNotFoundError(
                "no draft in progress — run `draft.py start` first"
            )
        return cls(**json.loads(SESSION_PATH.read_text()))

    @classmethod
    def start(cls, **kw) -> "Session":
        s = cls(created=datetime.now().isoformat(timespec="seconds"), **kw)
        s.save()
        return s


# ---------------------------------------------------------------------------
# Roster evaluation
# ---------------------------------------------------------------------------

def optimal_lineup(
    roster: pl.DataFrame, settings: LeagueSettings, points_col: str = "projected_points"
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split a roster into the best legal starting lineup and the bench.

    Fills dedicated slots first with the best player at each position, then
    lets FLEX take the best remaining eligible player. Greedy is optimal here
    because FLEX is strictly less constrained than the dedicated slots.
    """
    if not roster.height:
        return roster, roster

    remaining = roster.sort(points_col, descending=True, nulls_last=True)
    starters = []

    for slot, count in settings.lineup.items():
        if slot in ("FLEX", "SUPERFLEX"):
            continue
        pool = remaining.filter(pl.col("position") == slot).head(count)
        if pool.height:
            # Label which slot each starter is filling. A lineup is positions,
            # not a list -- "RB2" and "FLEX" are different jobs.
            starters.append(pool.with_columns(
                (pl.lit(slot) + (pl.int_range(pl.len()) + 1).cast(pl.Utf8)
                 if count > 1 else pl.lit(slot)).alias("slot")
            ))
            remaining = remaining.filter(~pl.col("player_id").is_in(pool["player_id"]))

    flex = settings.lineup.get("FLEX", 0)
    if flex:
        pool = remaining.filter(
            pl.col("position").is_in(list(settings.flex_eligible))
        ).head(flex)
        if pool.height:
            starters.append(pool.with_columns(pl.lit("FLEX").alias("slot")))
            remaining = remaining.filter(~pl.col("player_id").is_in(pool["player_id"]))

    start_df = pl.concat(starters) if starters else roster.head(0)
    return start_df, remaining


def grade_roster(
    roster: pl.DataFrame, settings: LeagueSettings, board: pl.DataFrame
) -> dict:
    """Score a roster: starting strength, risk shape, and unfilled slots.

    The benchmark is an even share of the board's total value. A team holding
    exactly its share of available value grades at 100.
    """
    if not roster.height:
        return {"error": "no players yet"}

    starters, bench = optimal_lineup(roster, settings)

    total = float(starters["projected_points"].sum()) if starters.height else 0.0
    vor = float(starters["vor"].sum()) if "vor" in starters.columns else None

    floor = ceiling = None
    if "season_p20" in starters.columns:
        floor = float(starters["season_p20"].drop_nulls().sum())
        ceiling = float(starters["season_p80"].drop_nulls().sum())

    # Par: an even share of startable value across the league.
    n_start = sum(v for k, v in settings.lineup.items() if k not in ("K", "DST"))
    par_pool = (
        board.filter(pl.col("position").is_in(list(config.MODELED_POSITIONS)))
        .sort("projected_points", descending=True)
        .head(n_start * settings.n_teams)
    )
    par = float(par_pool["projected_points"].sum()) / settings.n_teams if par_pool.height else 0.0

    needs = {}
    counts = {}
    for pos in starters["position"].to_list() + bench["position"].to_list():
        counts[pos] = counts.get(pos, 0) + 1
    for slot, n in settings.lineup.items():
        if slot in ("FLEX", "SUPERFLEX"):
            continue
        short = n - counts.get(slot, 0)
        if short > 0:
            needs[slot] = short

    risk = None
    if {"season_range", "season_p50"}.issubset(board.columns) and starters.height:
        # Coefficient of variation, not raw range -- see engine.recommend.
        pct = board.with_columns(
            pl.when(pl.col("season_p50") > 0)
            .then(pl.col("season_range") / pl.col("season_p50"))
            .otherwise(None).alias("_cv")
        ).with_columns(
            (pl.col("_cv").rank("average") / pl.len()).alias("_p")
        ).filter(pl.col("player_id").is_in(starters["player_id"]))["_p"].drop_nulls()
        if pct.len():
            risk = round(float(pct.mean()), 3)

    return {
        "players": roster.height,
        "starters": starters.height,
        "starter_points": round(total, 1),
        "starter_vor": round(vor, 1) if vor is not None else None,
        "season_floor": round(floor, 0) if floor else None,
        "season_ceiling": round(ceiling, 0) if ceiling else None,
        "par": round(par, 1),
        "score": round(100 * total / par, 1) if par else None,
        "risk_profile": risk,
        "unfilled": needs,
        "lineup": starters,
        "bench": bench,
    }
