"""Draft assistant: who to take, given where you are and what you have.

Three ideas do the work.

1. VALUE OVER REPLACEMENT. Raw projected points can't compare a WR to a TE.
   What matters is points above the worst player you'd be *forced* to start
   at that position, which falls out of league size and lineup.

2. SURVIVAL. The question at 1.07 is not "who is best" but "who is best that
   will not last until my next pick". At the turn in a 12-team league you
   wait 22 picks; mid-round you wait 12. Taking someone who would still be
   there is a wasted pick, and the gap is what decides it.

3. VOLATILITY BY ROUND. Early picks should skew low-variance — those are the
   anchors you need a floor from. Late picks should skew high-variance,
   because a bust in round 13 costs nothing and a hit wins the league. This
   is mean-variance applied to roster construction, and it is what keeps the
   board from being either too conservative or too wild.

Nothing here assumes a particular league. Pass your own LeagueSettings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import polars as pl

from fantasyedge.league import (
    LeagueSettings,
    overall_pick,
    picks_until_next,
    slot_for_pick,
)

# Actual draft position deviates from expert consensus by more than the
# experts deviate from each other. This widens the ECR spread into something
# closer to observed draft-day variance.
SD_TO_DRAFT_SIGMA = 2.5
MIN_DRAFT_SIGMA = 2.0


@dataclass
class DraftState:
    """Where the draft is and what you own."""

    settings: LeagueSettings
    my_slot: int
    drafted: list[str] = field(default_factory=list)   # player_id, in order
    my_roster: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 1 <= self.my_slot <= self.settings.n_teams:
            raise ValueError(
                f"slot {self.my_slot} outside 1..{self.settings.n_teams}"
            )

    @property
    def picks_made(self) -> int:
        return len(self.drafted)

    def on_the_clock(self) -> tuple[int, int]:
        """(round, pick) currently up, 1-indexed."""
        nxt = self.picks_made + 1
        rnd = (nxt - 1) // self.settings.n_teams + 1
        pick = (nxt - 1) % self.settings.n_teams + 1
        return rnd, pick

    def is_my_turn(self) -> bool:
        rnd, pick = self.on_the_clock()
        return slot_for_pick(rnd, pick, self.settings.n_teams) == self.my_slot


# ---------------------------------------------------------------------------
# Replacement level and VOR
# ---------------------------------------------------------------------------

def replacement_points(
    projections: pl.DataFrame,
    settings: LeagueSettings,
    points_col: str = "projected_points",
) -> dict[str, float]:
    """Points scored by the last startable player at each position."""
    out: dict[str, float] = {}
    for pos, rank in settings.replacement_ranks().items():
        pool = (
            projections.filter(pl.col("position") == pos)
            .sort(points_col, descending=True)
        )
        if not pool.height:
            continue
        idx = min(rank, pool.height) - 1
        out[pos] = float(pool[points_col][idx])
    return out


def add_vor(
    projections: pl.DataFrame,
    settings: LeagueSettings,
    points_col: str = "projected_points",
) -> pl.DataFrame:
    """Attach value over replacement. This is what makes positions comparable."""
    repl = replacement_points(projections, settings, points_col)
    expr = pl.lit(None, dtype=pl.Float64)
    for pos, pts in repl.items():
        expr = pl.when(pl.col("position") == pos).then(
            pl.col(points_col) - pts
        ).otherwise(expr)
    return projections.with_columns(expr.alias("vor"))


# ---------------------------------------------------------------------------
# Survival
# ---------------------------------------------------------------------------

def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def survival_probability(
    ecr: float, sd: float | None, picks_ahead: int, current_overall: int
) -> float:
    """P(player is still on the board when you pick again).

    Treats actual draft position as roughly normal around ECR. A tight
    consensus goes off the board predictably; a high-`sd` player is a coin
    flip, which is exactly the uncertainty worth acting on.
    """
    if ecr is None:
        return 0.5
    sigma = max(MIN_DRAFT_SIGMA, (sd or 0.0) * SD_TO_DRAFT_SIGMA)
    target = current_overall + picks_ahead
    # P(draft position > target)
    return 1.0 - _norm_cdf((target - ecr) / sigma)


# ---------------------------------------------------------------------------
# Roster need and risk shaping
# ---------------------------------------------------------------------------

def roster_needs(state: DraftState, roster_positions: list[str]) -> dict[str, int]:
    """Unfilled starting slots by position."""
    counts: dict[str, int] = {}
    for pos in roster_positions:
        counts[pos] = counts.get(pos, 0) + 1

    needs: dict[str, int] = {}
    for slot, n in state.settings.lineup.items():
        if slot in ("FLEX", "SUPERFLEX"):
            continue
        needs[slot] = max(0, n - counts.get(slot, 0))

    flex = state.settings.lineup.get("FLEX", 0)
    if flex:
        surplus = sum(
            max(0, counts.get(p, 0) - state.settings.lineup.get(p, 0))
            for p in state.settings.flex_eligible
        )
        needs["FLEX"] = max(0, flex - surplus)
    return needs


def need_multiplier(position: str, needs: dict[str, int], round_no: int) -> float:
    """Weight a position by how badly the roster still needs it.

    Deliberately gentle early — chasing need in round 2 is how people reach
    for a bad TE. It sharpens later, when unfilled starting slots become a
    real problem.
    """
    unfilled = needs.get(position, 0)
    flex_open = needs.get("FLEX", 0) > 0 and position in ("RB", "WR", "TE")

    if unfilled > 0:
        base = 1.0 + min(0.35, 0.10 * unfilled)
    elif flex_open:
        base = 1.05
    else:
        base = 0.80  # already covered; only take clear value

    urgency = min(1.0, round_no / 8.0)
    return 1.0 + (base - 1.0) * urgency


def target_volatility(round_no: int, n_rounds: int, tolerance: str) -> float:
    """Desired volatility percentile for this round, 0 = floor, 1 = upside.

    Ramps from safe to swingy across the draft. `tolerance` shifts the whole
    curve for someone who wants a generally safer or wilder roster.
    """
    progress = (round_no - 1) / max(1, n_rounds - 1)
    base = 0.25 + 0.5 * progress
    shift = {"conservative": -0.15, "balanced": 0.0, "aggressive": 0.15}
    return min(1.0, max(0.0, base + shift.get(tolerance, 0.0)))


def risk_fit(player_vol_pct: float | None, target: float) -> float:
    """How well a player's volatility matches what this round wants."""
    if player_vol_pct is None:
        return 1.0
    return 1.0 - 0.30 * abs(player_vol_pct - target)


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

def recommend(
    state: DraftState,
    projections: pl.DataFrame,
    n: int = 3,
    exclude: list[str] | None = None,
    risk_tolerance: str = "balanced",
) -> pl.DataFrame:
    """Top `n` picks for whoever is on the clock.

    `exclude` is the re-roll: pass the players you were just shown (or that
    got sniped) and ask again.

    `projections` needs: player_id, player_name, position, projected_points,
    ecr. Optional: sd, floor, ceiling.
    """
    exclude = exclude or []
    settings = state.settings
    rnd, pick = state.on_the_clock()
    current = overall_pick(rnd, pick, settings.n_teams)
    gap = picks_until_next(state.my_slot, current, settings.n_teams,
                           settings.n_rounds)

    avail = projections.filter(
        ~pl.col("player_id").is_in(state.drafted + exclude)
    )
    if not avail.height:
        return avail

    avail = add_vor(avail, settings)

    # Volatility percentile within position, so a "high variance" TE is
    # judged against TEs rather than against QBs.
    if "ceiling" in avail.columns and "floor" in avail.columns:
        avail = avail.with_columns(
            (pl.col("ceiling") - pl.col("floor")).alias("_spread")
        ).with_columns(
            (pl.col("_spread").rank("average").over("position")
             / pl.len().over("position")).alias("vol_pct")
        )
    else:
        avail = avail.with_columns(pl.lit(None, dtype=pl.Float64).alias("vol_pct"))

    roster_pos = (
        projections.filter(pl.col("player_id").is_in(state.my_roster))["position"]
        .to_list()
    )
    needs = roster_needs(state, roster_pos)
    target = target_volatility(rnd, settings.n_rounds, risk_tolerance)

    rows = []
    for r in avail.iter_rows(named=True):
        if r.get("vor") is None:
            continue
        p_survive = (
            survival_probability(r.get("ecr"), r.get("sd"), gap, current)
            if gap is not None else 0.0
        )
        urgency = 1.0 - p_survive
        nm = need_multiplier(r["position"], needs, rnd)
        rf = risk_fit(r.get("vol_pct"), target)

        # Value, weighted by how badly you need the position, how well the
        # risk profile fits this round, and how likely he is to be gone.
        score = r["vor"] * nm * rf * (1.0 + 0.6 * urgency)

        rows.append({
            "player_name": r.get("player_name"),
            "position": r["position"],
            "ecr": r.get("ecr"),
            "projected_points": r.get("projected_points"),
            "vor": round(r["vor"], 1),
            "p_survive": round(p_survive, 3),
            "need_mult": round(nm, 3),
            "risk_fit": round(rf, 3),
            "score": round(score, 1),
            "player_id": r["player_id"],
        })

    if not rows:
        return pl.DataFrame()

    out = pl.DataFrame(rows).sort("score", descending=True).head(n)
    return out.with_columns([
        pl.lit(rnd).alias("round"),
        pl.lit(pick).alias("pick"),
        pl.lit(current).alias("overall"),
        pl.lit(gap).alias("picks_until_next"),
        pl.lit(round(target, 2)).alias("target_vol_pct"),
    ])
