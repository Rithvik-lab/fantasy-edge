"""Kickers and defences: the two positions the rest of this engine ignores.

WHY THEY WERE MISSING, AND WHY THAT WAS FATAL

Every model here is built on skill positions, and for good reason -- that is
where a draft is won. So the ESPN pull filtered to QB/RB/WR/TE and the
per-game curve was only ever fitted on those four. The consequence went
unnoticed for a long time: there was not a single kicker or defence anywhere on
the board, in a league that starts one of each.

Following the app's own advice for sixteen straight picks therefore produced
eight running backs, six receivers, and a roster that could not field a legal
lineup. The engine was not wrong about value. It was answering a question with
half the positions removed.

WHAT THESE NUMBERS ARE

Measured on 2025, not invented, but deliberately coarse:

    K       from fg_made * 3 + pat_made, over kickers with 12+ games
            K1 206, K6 146, K12 130, K24 106

    DST     from sacks, interceptions, fumble recoveries, defensive
            touchdowns and safeties
            DST1 110, DST6 95, DST12 77, DST32 28

The defence figures omit points-allowed tiers, which need schedule joins and
add roughly a flat 80-110 to every team -- that moves the level, not the order,
and order is all this is used for.

THE SPREAD IS THE POINT

K1 beats K12 by about 4.5 points a week, and next year's K1 is close to
unknowable from this year's. That is precisely why these go in the last two
rounds and why the engine should never recommend one early. This module exists
so they can be TAKEN, not so they can be chased.
"""

from __future__ import annotations

import polars as pl

# INDEXED BY DRAFT ORDER, NOT BY FINISH -- and the difference is the whole
# reason these numbers look the way they do.
#
# The measured 2025 spread by REALIZED finish is steep: K1 206, K12 130, K24
# 106. Feeding that in as a projection was the same mistake this project made
# once before with the per-game curve, and it produced the same absurdity: the
# best kicker came out 14th overall by value, because a curve keyed on outcome
# quietly assumes you can pick the winner in August.
#
# You cannot. Kicker finish is close to independent of where kickers are
# drafted, so the honest expectation for whoever you take is near the middle of
# the pool wherever you take him, with the observed spread showing up as
# VARIANCE rather than as expected points. Hence nearly flat, with a small tilt
# that reflects the little the market does know (a good offence kicks more
# extra points) rather than the outcome it cannot know.
K_CURVE: tuple[tuple[int, float], ...] = (
    (1, 148.0), (6, 143.0), (12, 138.0), (20, 130.0), (32, 120.0),
)

# Counting stats only; see the docstring. The constant lifts the level to
# roughly where a full-scoring defence lands so it is comparable to the other
# positions on the board rather than looking worthless beside them.
# Same treatment, same reason. Realized 2025 by finish ran DST1 110 to DST32
# 28 on counting stats; defence finish is at least as unpredictable as kicker
# finish, so the draftable expectation is flat with a slight tilt.
DST_CURVE: tuple[tuple[int, float], ...] = (
    (1, 52.0), (6, 48.0), (12, 44.0), (20, 38.0), (32, 30.0),
)
DST_BASELINE = 95.0     # points-allowed contribution, flat across teams

# How wide a season is at these positions, as a fraction of the projection.
# Wide, and honestly so -- this is where the realized spread went once it was
# taken out of the expectation. A kicker really can return 206 or 106; what you
# cannot do is know which in August.
SPREAD = 0.34


def _interp(curve: tuple[tuple[int, float], ...], rank: int) -> float:
    xs = [c[0] for c in curve]
    ys = [c[1] for c in curve]
    if rank <= xs[0]:
        return ys[0]
    if rank >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if rank <= xs[i]:
            span = xs[i] - xs[i - 1]
            t = (rank - xs[i - 1]) / span if span else 0.0
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return ys[-1]


def project(position: str, pos_rank: int) -> float:
    """Season points for the nth kicker or defence off the board."""
    if position == "K":
        return _interp(K_CURVE, max(1, pos_rank))
    if position == "DST":
        return _interp(DST_CURVE, max(1, pos_rank)) + DST_BASELINE
    return 0.0


def rows(market: pl.DataFrame) -> pl.DataFrame:
    """Board rows for whichever kickers and defences the market carries."""
    kd = market.filter(pl.col("position").is_in(["K", "DST"]))
    if not kd.height:
        return pl.DataFrame()

    # NEITHER HAS A GSIS ID, and a defence never will -- it is a team, not a
    # person, and the crosswalk is a person-level table. Kickers are missing
    # for a duller reason: nobody builds crosswalks for them. So both are keyed
    # off the ESPN id, prefixed so it can never collide with a real gsis.
    kd = kd.with_columns([
        pl.col("adp").rank("ordinal").over("position").cast(pl.Int32).alias("pr"),
        pl.coalesce([pl.col("gsis_id"),
                     pl.lit("espn-") + pl.col("espn_id")]).alias("gsis_id"),
    ])
    pts = [project(r["position"], r["pr"]) for r in kd.iter_rows(named=True)]
    return kd.with_columns(pl.Series("projected_points", pts)).with_columns([
        (pl.col("projected_points") * (1 - SPREAD)).alias("season_p20"),
        pl.col("projected_points").alias("season_p50"),
        (pl.col("projected_points") * (1 + SPREAD)).alias("season_p80"),
        pl.lit(16.5).alias("expected_games"),
        pl.lit(False).alias("rookie"),
    ])


# ---------------------------------------------------------------------------
# WEEKLY, which is a completely different question from the season curve above
# ---------------------------------------------------------------------------
# The curves above are flat because season-long finish at these positions is
# close to unpredictable in August. Week to week is the opposite: the betting
# market prices every game before it is played, and those numbers carry real
# signal about how a kicker and a defence will score. This is the foundation
# waiver mode needs -- streaming K and DST is the most repeatable edge in the
# format, and it is repeatable precisely BECAUSE the season-long ranking is not.
#
# Fitted on 2022-2025, every team-week with a published line:
#
#   KICKER   pts = 3.38 + 0.174 * own team's implied points     n = 2,277
#   DEFENCE  pts = 14.02 - 0.411 * opponent's implied points    n = 2,278
#
# The defence slope is the one that matters. r = -0.310, and the softest
# quartile of matchup returns 6.82 a week against 3.02 for the toughest -- a
# 3.81 point gap, which is roughly 65 points across a season, or the difference
# between a good starter and a mediocre one at a real position.
#
# THAT NUMBER WAS 1.36 UNTIL POINTS-ALLOWED TIERS WENT IN. Scoring a defence on
# sacks and turnovers alone hides most of the effect, because the largest part
# of what a soft matchup buys you is simply the opponent failing to score. It
# is worth naming: measuring the wrong scoring rules understated the edge by
# a factor of nearly three, and would have made streaming look not worth doing.
K_INTERCEPT, K_SLOPE = 3.38, 0.174
DST_INTERCEPT, DST_SLOPE = 14.02, -0.411


def project_week(position: str, implied_for: float | None = None,
                 implied_against: float | None = None) -> float | None:
    """Points for one kicker or defence in one game, from the betting line.

    `implied_for` is what the market expects this team to score; the opponent's
    number goes in `implied_against`. Both come straight out of a spread and a
    total: total/2 +- spread/2.

    Returns None when there is no line, which is honest -- before a game is
    priced there is nothing here the season curve does not already say.
    """
    if position == "K":
        if implied_for is None:
            return None
        return K_INTERCEPT + K_SLOPE * float(implied_for)
    if position == "DST":
        if implied_against is None:
            return None
        return DST_INTERCEPT + DST_SLOPE * float(implied_against)
    return None


def implied(total_line: float, spread_line: float) -> tuple[float, float]:
    """(this team's implied points, the opponent's) from a line.

    `spread_line` is from this team's perspective: negative when favoured.
    """
    return (total_line / 2 + spread_line / 2,
            total_line / 2 - spread_line / 2)


# ---------------------------------------------------------------------------
# The fine print: which opponents are soft, beyond what the line already says
# ---------------------------------------------------------------------------
# The betting line prices most of a matchup, so "who allows the most kicker
# points" is mostly just "who is bad" and the market has taken that already.
# What is left is the RESIDUAL -- teams whose opponents beat the fitted model
# week after week. That is the part worth streaming on.
#
# Measured 2022-2025, and then tested for whether it is real rather than noise.
# Split-half across a two-year gap, 2022-23 against 2024-25:
#
#     kickers allowed, residual    r = +0.304
#     defences allowed, residual   r = +0.233
#     implied points (control)     r = +0.294
#
# The control matters. Team quality itself only persists at 0.294 over that
# gap -- rosters turn over -- so a matchup edge persisting at 0.30 and 0.23 is
# about as stable as anything team-level gets. It is a real effect.
#
# It is also SMALL: sd 0.52 points a week across defences for kickers, 0.73
# across offences for defences. Against the 3.81-point range the line itself
# explains, this is a tiebreaker between two similar streams, not a reason to
# start a bad one. Sized accordingly, and computed from the pulled data rather
# than frozen into a table here, because rosters change and a hardcoded list of
# 32 teams would quietly rot.
MIN_GAMES_FOR_EDGE = 25


def opponent_edge(kicker_weeks: pl.DataFrame | None = None,
                  defence_weeks: pl.DataFrame | None = None) -> dict[str, dict[str, float]]:
    """Per-opponent residual, in points a week.

    Each frame needs `opp`, `pts` and `expected`. Positive means opponents of
    that team beat the model there -- a soft draw.
    """
    out: dict[str, dict[str, float]] = {"K": {}, "DST": {}}
    for key, df in (("K", kicker_weeks), ("DST", defence_weeks)):
        if df is None or not df.height:
            continue
        if not {"opp", "pts", "expected"} <= set(df.columns):
            continue
        agg = (df.with_columns((pl.col("pts") - pl.col("expected")).alias("_r"))
                 .group_by("opp")
                 .agg(pl.col("_r").mean().alias("edge"), pl.len().alias("n"))
                 .filter(pl.col("n") >= MIN_GAMES_FOR_EDGE))
        out[key] = {r["opp"]: round(float(r["edge"]), 2)
                    for r in agg.iter_rows(named=True)}
    return out


def stream_score(position: str, opponent: str,
                 implied_for: float | None = None,
                 implied_against: float | None = None,
                 edges: dict[str, dict[str, float]] | None = None) -> float | None:
    """Weekly projection with the opponent adjustment folded in.

    This is the number a waiver decision should be made on: what the line says,
    plus the small persistent thing it does not.
    """
    base = project_week(position, implied_for, implied_against)
    if base is None:
        return None
    bump = (edges or {}).get(position, {}).get(opponent, 0.0)
    return base + bump
