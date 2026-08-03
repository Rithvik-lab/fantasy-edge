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

# How much positional scarcity shifts the score. At 0.4 the scarcest position
# gets a 40% bump over the flattest, which is enough to break ties in favour
# of a cliff position without letting scarcity override raw value.
DROPOFF_WEIGHT = 0.4

# Extra volatility tolerated on bench picks, for the convexity reason above.
BENCH_VARIANCE_BONUS = 0.20
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


def target_volatility(
    round_no: int,
    n_rounds: int,
    tolerance: str,
    roster_risk: float | None = None,
    roster_strength: float | None = None,
    filling_starter: bool = True,
) -> float:
    """Desired volatility percentile for this pick, 0 = floor, 1 = upside.

    Two inputs, and the second is what makes this portfolio construction
    rather than a ranked list.

    ROUND. Ramps from safe to swingy across the draft. Early picks are the
    anchors you need a floor from; a bust in round 13 costs nothing and a hit
    wins the league.

    ROSTER. What you already own changes what you should want next. Take a
    metronome like Jeanty (season sd ~31) and you have bought certainty, so
    you can afford to swing on the next pick. Take three boom-bust players
    and you need a floor regardless of what round it is -- the marginal value
    of a player depends on the portfolio he is joining, not just on him.

    `roster_risk` is the mean volatility percentile of what you have drafted,
    on the same 0-1 scale. Passing None keeps the old round-only behaviour.
    """
    progress = (round_no - 1) / max(1, n_rounds - 1)
    base = 0.25 + 0.5 * progress
    shift = {"safe": -0.20, "conservative": -0.20, "combined": 0.0,
             "balanced": 0.0, "aggressive": 0.20}
    target = base + shift.get(tolerance, 0.0)

    # A bench player's downside is capped -- you simply do not start a bust --
    # while his upside is not, because a hit becomes a starter. That payoff is
    # convex, so variance is worth more on the bench than in the lineup, and
    # the same nominal risk setting should mean something more aggressive
    # there.
    if not filling_starter:
        target += BENCH_VARIANCE_BONUS

    if roster_strength is not None:
        # Simulation-derived, not asserted. 3,000 simulated 12-team seasons
        # per cell say the crossover sits at league average:
        #
        #   strength  best sd  playoff%
        #      -15      38      24.3%     weak rosters want variance
        #       -5      38      47.1%
        #        0      26      60.4%     crossover
        #       +5      18      77.1%     strong rosters want consistency
        #      +15      18      95.1%
        #
        # And it is asymmetric -- at -15 volatility doubles playoff odds
        # (11.8% -> 24.3%) while at +15 consistency buys only six points. So
        # a trailing roster should swing harder than a leading one plays safe.
        from fantasyedge.models.roster_strategy import recommended_volatility
        target = 0.5 * target + 0.5 * recommended_volatility(roster_strength)
    elif roster_risk is not None:
        # Fallback when strength is unknown: counterbalance what you hold.
        target += ROSTER_BALANCE_WEIGHT * (0.5 - roster_risk)

    return min(1.0, max(0.0, target))


# How hard to counterbalance the existing roster. At 0.5, a maximally safe
# roster moves the target a quarter of the scale -- enough to change which
# player wins a close call, not enough to override value.
ROSTER_BALANCE_WEIGHT = 0.5


def roster_risk_profile(
    projections: pl.DataFrame, my_roster: list[str]
) -> float | None:
    """Mean volatility percentile of what you have already drafted.

    Uses season-level spread where the simulation has run, falling back to
    per-game spread. None when the roster is empty or unmeasurable.
    """
    if not my_roster:
        return None

    owned = projections.filter(pl.col("player_id").is_in(my_roster))
    if not owned.height:
        return None

    if {"season_range", "season_p50"}.issubset(projections.columns):
        ranked = projections.with_columns(
            pl.when(pl.col("season_p50") > 0)
            .then(pl.col("season_range") / pl.col("season_p50"))
            .otherwise(None).alias("_cv")
        ).with_columns((pl.col("_cv").rank("average") / pl.len()).alias("_pct"))
        vals = ranked.filter(pl.col("player_id").is_in(my_roster))["_pct"].drop_nulls()
        if vals.len():
            return float(vals.mean())

    for col in ("_spread", "vol_pct"):
        if col in projections.columns:
            ranked = projections.with_columns(
                (pl.col(col).rank("average") / pl.len()).alias("_pct")
            )
            vals = ranked.filter(
                pl.col("player_id").is_in(my_roster)
            )["_pct"].drop_nulls()
            if vals.len():
                return float(vals.mean())
    return None


def risk_fit(player_vol_pct: float | None, target: float) -> float:
    """How well a player's volatility matches what this round wants."""
    if player_vol_pct is None:
        return 1.0
    return 1.0 - 0.30 * abs(player_vol_pct - target)


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

def expected_best_survivor(
    pool: pl.DataFrame, picks_ahead: int, current_overall: int
) -> float:
    """Expected VOR of the best player at a position who lasts until you pick again.

    Not simply the best available -- he probably will not be there. The best
    *survivor* is the first player, in value order, who happens to last:

        E[best] = sum_i  VOR_i * P(i survives) * prod_{j better} (1 - P(j survives))

    which is what you should actually compare against when deciding whether
    to take a position now.
    """
    if not pool.height:
        return 0.0

    ranked = pool.sort("vor", descending=True, nulls_last=True)
    expected = 0.0
    all_better_gone = 1.0

    for r in ranked.iter_rows(named=True):
        vor = r.get("vor")
        if vor is None:
            continue
        p = survival_probability(r.get("ecr"), r.get("sd"), picks_ahead,
                                 current_overall)
        expected += vor * p * all_better_gone
        all_better_gone *= (1.0 - p)
        if all_better_gone < 0.01:
            break
    return expected


def positional_dropoff(
    avail: pl.DataFrame, picks_ahead: int | None, current_overall: int,
    positions: list[str] | None = None,
) -> pl.DataFrame:
    """How much value you lose at each position by waiting one turn.

    This is the number that decides whether taking a tight end at 44 is smart
    or a reach. If the next tight end goes at 50, taking one now costs you the
    six players in between -- so it is only right when the tight end cliff is
    steeper than the drop-off at whatever else you would have taken.

    Positive `dropoff` means the position is scarce and worth acting on.
    """
    positions = positions or ["QB", "RB", "WR", "TE"]
    rows = []
    for pos in positions:
        pool = avail.filter(pl.col("position") == pos)
        if not pool.height:
            continue
        best_now = float(pool["vor"].max() or 0.0)
        later = (
            expected_best_survivor(pool, picks_ahead, current_overall)
            if picks_ahead is not None else 0.0
        )
        rows.append({
            "position": pos,
            "best_now": round(best_now, 1),
            "expected_later": round(later, 1),
            "dropoff": round(best_now - later, 1),
        })
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).sort("dropoff", descending=True)


def recommend(
    state: DraftState,
    projections: pl.DataFrame,
    n: int = 3,
    exclude: list[str] | None = None,
    risk_tolerance: str = "balanced",
    bench_tolerance: str | None = None,
    roster_strength: float | None = None,
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
    if "season_range" in avail.columns:
        # Normalise by the median season before ranking. Raw range makes any
        # high scorer look volatile -- McCaffrey's 291-372 band is wider in
        # absolute points than a late-round flier's 50-120 while being far
        # tighter relative to what he produces. Coefficient of variation is
        # the honest comparison.
        avail = avail.with_columns(
            pl.when(pl.col("season_p50") > 0)
            .then(pl.col("season_range") / pl.col("season_p50"))
            .otherwise(None)
            .alias("_cv")
        ).with_columns(
            (pl.col("_cv").rank("average").over("position")
             / pl.len().over("position")).alias("vol_pct")
        )
    elif "ceiling" in avail.columns and "floor" in avail.columns:
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
    roster_risk = roster_risk_profile(projections, state.my_roster)

    # Positional scarcity: what each position costs you if you wait a turn.
    dd = positional_dropoff(avail, gap, current)
    dropoff = dict(zip(dd["position"].to_list(), dd["dropoff"].to_list())) if dd.height else {}
    max_drop = max(dropoff.values()) if dropoff else 1.0
    # A pick fills a starting slot if any dedicated slot is still open;
    # otherwise it is bench depth and gets the bench risk setting.
    starters_open = any(v > 0 for k, v in needs.items() if k != "FLEX")
    tol = risk_tolerance if starters_open else (bench_tolerance or risk_tolerance)
    target = target_volatility(rnd, settings.n_rounds, tol, roster_risk,
                               roster_strength, filling_starter=starters_open)

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
        # risk profile fits this round, and how likely he is to be gone --
        # plus how much the position itself falls off if you wait. That last
        # term is the opportunity cost of reaching: taking a tight end early
        # is only right when the tight end cliff is steeper than the drop-off
        # at whatever you would otherwise have taken.
        drop = dropoff.get(r["position"], 0.0)
        scarcity = 1.0 + DROPOFF_WEIGHT * (drop / max(1.0, max_drop))
        score = r["vor"] * nm * rf * (1.0 + 0.6 * urgency) * scarcity

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
            "dropoff": round(dropoff.get(r["position"], 0.0), 1),
            "floor": round(r["season_p20"], 0) if r.get("season_p20") else None,
            "ceiling": round(r["season_p80"], 0) if r.get("season_p80") else None,
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
        pl.lit(round(roster_risk, 3) if roster_risk is not None else None)
          .alias("roster_risk"),
    ])
