"""Draft assistant: who to take, given where you are and what you have.

Three ideas do the work.

1. VALUE OVER REPLACEMENT. Raw projected points can't compare a WR to a TE.
   What matters is points above the worst player you'd be *forced* to start
   at that position, which falls out of league size and lineup.

2. THE PICK PAIR. The question at 1.07 is not "who is best" but "who is best
   that will not last until my next pick". At the turn in a 12-team league
   you wait 22 picks; mid-round you wait 12. Taking someone who would still
   be there spends a pick on nothing, so a player is scored on what he is
   worth now PLUS what the board still owes you at your next turn -- which
   is what charges you for reaching rather than just rewarding scarcity.

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

import numpy as np
import polars as pl

from fantasyedge.league import (
    LeagueSettings,
    gap_until_next,
    overall_pick,
    picks_for_slot,
    slot_for_pick,
)

# Actual draft position deviates from expert consensus by more than the
# experts deviate from each other. This widens the ECR spread into something
# closer to observed draft-day variance.
SD_TO_DRAFT_SIGMA = 2.5

# Retained for `positional_dropoff` reporting only. Scarcity used to enter the
# score as a multiplier here; it is now priced per player by
# `next_pick_value`, which does not need a hand-set weight.
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
    # Overall pick numbers you actually hold. None means the plain snake off
    # `my_slot`; pass a list once picks have been traded.
    owned_picks: list[int] | None = None
    # Plan for a pick other than the one up now. Everything downstream --
    # the wait until your next turn, and therefore every survival probability
    # -- keys off the pick being evaluated, so overriding it here is what makes
    # "what should I be aiming at in round five" a real question rather than a
    # relabelled version of the current answer.
    at_overall: int | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.my_slot <= self.settings.n_teams:
            raise ValueError(
                f"slot {self.my_slot} outside 1..{self.settings.n_teams}"
            )

    @property
    def picks_made(self) -> int:
        return len(self.drafted)

    def my_picks(self) -> list[int]:
        """Every overall pick you hold, traded or not."""
        if self.owned_picks is not None:
            return sorted(self.owned_picks)
        return picks_for_slot(self.my_slot, self.settings.n_teams,
                              self.settings.n_rounds)

    def on_the_clock(self) -> tuple[int, int]:
        """(round, pick) being evaluated, 1-indexed."""
        nxt = self.at_overall if self.at_overall is not None else self.picks_made + 1
        rnd = (nxt - 1) // self.settings.n_teams + 1
        pick = (nxt - 1) % self.settings.n_teams + 1
        return rnd, pick

    def is_my_turn(self) -> bool:
        rnd, pick = self.on_the_clock()
        if self.owned_picks is not None:
            return overall_pick(rnd, pick, self.settings.n_teams) in self.owned_picks
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
    replacement: dict[str, float] | None = None,
) -> pl.DataFrame:
    """Attach value over replacement. This is what makes positions comparable.

    Pass `replacement` when scoring a SUBSET of the board. Replacement level is
    a property of the league -- the worst player you could be forced to start
    all season -- so it must come from the whole player pool. Deriving it from
    whoever happens to be undrafted lets it collapse as the board empties, and
    every VOR inflates with it:

        after picks      QB      RB      WR      TE
                  0   267.1   147.7   178.0   138.1
                110   125.7    41.1    79.1    88.5

    Jared Goff was worth -38.8 against the real bar and +102.7 against the
    pick-111 remnant, which is how a quarterback nobody needed kept arriving
    at the top of round 10.
    """
    repl = replacement or replacement_points(projections, settings, points_col)
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


def weighted(vor: float, *multipliers: float) -> float:
    """Apply multiplicative weights to VOR without inverting on negatives.

    Late in a draft most of the board really is below replacement, so VOR
    goes negative -- and a multiplier flips meaning there. Weighting a needed
    position by 1.2 turns -10 into -12, ranking it BELOW a position you have
    already filled. The weights would start recommending the worst player at
    whatever you most need.

    Only the surplus above replacement gets weighted; the deficit passes
    through untouched. Continuous at zero and identical to plain multiplication
    everywhere VOR is positive, which is every pick that matters.
    """
    surplus = max(vor, 0.0)
    for m in multipliers:
        surplus *= m
    return surplus + min(vor, 0.0)


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


def _consume(needs: dict[str, int], position: str,
             flex_eligible: tuple[str, ...]) -> dict[str, int]:
    """Roster needs after drafting one player at `position`."""
    out = dict(needs)
    if out.get(position, 0) > 0:
        out[position] -= 1
    elif position in flex_eligible and out.get("FLEX", 0) > 0:
        out["FLEX"] -= 1
    return out


def next_pick_value(
    avail: pl.DataFrame,
    needs: dict[str, int],
    round_no: int,
    picks_ahead: int | None,
    current_overall: int,
    flex_eligible: tuple[str, ...] = ("RB", "WR", "TE"),
) -> dict[str, float]:
    """For each candidate, the expected value of your NEXT pick if you take him.

    THE OPPORTUNITY COST OF REACHING

    Scoring players one at a time cannot see a reach. A player who will still
    be on the board in ten picks scores exactly like one who will not, so the
    engine kept recommending players it could simply have waited for -- paying
    a pick for something that was free.

    The old fix was a bonus: multiply by how unlikely a player is to survive.
    That ranks the scarce player higher but never charges you for the reach,
    so with enough raw value a player who would obviously last still wins.

    Pricing the PAIR does charge you. You hold this pick and your next one, so
    the real choice is

        take i now  ->  VOR_i  +  E[best available at my next pick, without i]

    Take someone who would have lasted and the second term barely moves -- he
    was going to be there anyway, so all you did was spend a pick early and
    give up whoever you could have had now. Take someone who would NOT have
    lasted and the second term drops by real value, which is precisely what
    makes him worth taking now.

    Computed exactly, not approximated. With the pool in value order,

        E[best survivor] = sum_i  v_i p_i prod_{j<i} (1 - p_j)

    and dropping one player m out of that sum is a prefix/suffix identity:

        E[best | m gone] = prefix_val[m] + prefix_gone[m] * suffix[m+1]

    so the whole board costs one pass per position instead of one pass per
    candidate. Need is re-evaluated inside each scenario, because taking a
    running back changes what the next pick is worth to you.
    """
    if picks_ahead is None or not avail.height:
        return {}

    ids = avail["player_id"].to_list()
    poss = avail["position"].to_list()
    vor = np.nan_to_num(avail["vor"].cast(pl.Float64).to_numpy(), nan=0.0)
    surv = np.array([
        survival_probability(e, s, picks_ahead, current_overall)
        for e, s in zip(avail["ecr"].to_list(), avail["sd"].to_list())
    ])

    out: dict[str, float] = {}
    for taken in set(poss):
        after = _consume(needs, taken, flex_eligible)
        mult = np.array([need_multiplier(q, after, round_no + 1) for q in poss])
        v = np.maximum(vor, 0.0) * mult + np.minimum(vor, 0.0)  # see `weighted`

        order = np.argsort(-v)
        vs, ps = v[order], surv[order]
        n = len(vs)

        # suffix[k] = E[best survivor] looking only at players k onward
        suffix = np.zeros(n + 1)
        for k in range(n - 1, -1, -1):
            suffix[k] = vs[k] * ps[k] + (1.0 - ps[k]) * suffix[k + 1]

        # prefix_val[k] = value already accounted for by players before k,
        # prefix_gone[k] = P(all of them are gone)
        gone = np.ones(n + 1)
        pref = np.zeros(n + 1)
        for k in range(n):
            pref[k + 1] = pref[k] + vs[k] * ps[k] * gone[k]
            gone[k + 1] = gone[k] * (1.0 - ps[k])

        without = pref[:n] + gone[:n] * suffix[1:]
        for rank_idx, orig in enumerate(order):
            if poss[orig] == taken:
                out[ids[orig]] = float(without[rank_idx])
    return out


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
    # Gap comes from the picks you actually hold. Trade round five away and
    # the wait from round four doubles, which changes every survival
    # probability on the board.
    gap = gap_until_next(state.my_picks(), current)

    avail = projections.filter(
        ~pl.col("player_id").is_in(state.drafted + exclude)
    )
    if not avail.height:
        return avail

    # ---------------------------------------------------------------------
    # A LEGAL LINEUP IS A CONSTRAINT, NOT A PREFERENCE.
    #
    # Value alone will never tell you to draft a kicker, and it is right not
    # to: replacement level at K is so close to the best K that his VOR is
    # deeply negative, and no need multiplier survives being applied to a
    # negative number. Follow the ranking for sixteen straight picks and you
    # end up with eight running backs, six receivers, and no quarterback,
    # kicker or defence -- a roster that cannot field a legal lineup at all.
    #
    # So the last few picks are not a ranking problem. Once the picks you have
    # left are down to the slots you still cannot fill, those positions are the
    # only candidates. It binds only at the very end, which is exactly when a
    # manager stops asking "who is best" and starts asking "what do I still
    # need".
    roster_pos = (
        projections.filter(pl.col("player_id").is_in(state.my_roster))
        ["position"].to_list() if state.my_roster else []
    )
    open_slots = roster_needs(state, roster_pos)
    must = {p: n for p, n in open_slots.items()
            if n > 0 and p not in ("FLEX", "SUPERFLEX")}
    # ROSTER SPOTS LEFT, not picks left. The first version counted picks
    # remaining on the snake schedule, which is only meaningful when every pick
    # in the league is being recorded -- draft only your own men and `current`
    # walks one at a time while the schedule still says you hold fifteen more
    # turns. The constraint never bound and the roster still finished with no
    # quarterback. Spots on the roster are true in either mode.
    spots_left = settings.roster_size - len(state.my_roster)
    if must and spots_left <= sum(must.values()):
        forced = avail.filter(pl.col("position").is_in(list(must)))
        if forced.height:
            avail = forced

    # Replacement level from the FULL board, not from who is left. See add_vor.
    avail = add_vor(avail, settings,
                    replacement=replacement_points(projections, settings))

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

    # Positional scarcity, reported for context. It no longer feeds the score
    # -- `next_pick_value` measures the same thing per player and exactly.
    dd = positional_dropoff(avail, gap, current)
    dropoff = dict(zip(dd["position"].to_list(), dd["dropoff"].to_list())) if dd.height else {}
    # A pick fills a starting slot if any dedicated slot is still open;
    # otherwise it is bench depth and gets the bench risk setting.
    #
    # Only count slots you could actually fill from this board. Kickers and
    # defences are not modelled, so K and DST sat unfilled for the entire
    # draft -- which made this always True, pinned every pick to the starter
    # risk setting, and left `bench_tolerance` and BENCH_VARIANCE_BONUS as
    # dead code.
    draftable = set(avail["position"].unique().to_list())
    starters_open = any(
        v > 0 for k, v in needs.items() if k != "FLEX" and k in draftable
    )
    tol = risk_tolerance if starters_open else (bench_tolerance or risk_tolerance)
    target = target_volatility(rnd, settings.n_rounds, tol, roster_risk,
                               roster_strength, filling_starter=starters_open)

    # What your next pick is worth, for every possible choice here. This is
    # the term that charges you for reaching instead of merely rewarding
    # scarcity, so it replaces the old urgency and dropoff multipliers --
    # keeping those alongside it would price the same effect twice.
    nxt = next_pick_value(avail, needs, rnd, gap, current, settings.flex_eligible)

    rows = []
    for r in avail.iter_rows(named=True):
        if r.get("vor") is None:
            continue
        p_survive = (
            survival_probability(r.get("ecr"), r.get("sd"), gap, current)
            if gap is not None else 0.0
        )
        nm = need_multiplier(r["position"], needs, rnd)
        rf = risk_fit(r.get("vol_pct"), target)

        # Two picks, one number: what he is worth to you now, plus what the
        # board still owes you afterwards.
        now = weighted(r["vor"], nm, rf)
        later = nxt.get(r["player_id"], 0.0)

        rows.append({
            "player_name": r.get("player_name"),
            "position": r["position"],
            "ecr": r.get("ecr"),
            "projected_points": r.get("projected_points"),
            "vor": round(r["vor"], 1),
            "p_survive": round(p_survive, 3),
            "need_mult": round(nm, 3),
            "risk_fit": round(rf, 3),
            "now": round(now, 1),
            "next_pick": round(later, 1),
            "score": round(now + later, 1),
            "player_id": r["player_id"],
            "vol_pct": (round(r["vol_pct"], 3)
                        if r.get("vol_pct") is not None else None),
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
