"""What the other manager thinks a player is worth.

A trade needs two signatures. Ours is `trade.evaluate` -- what a deal does to
the lineup we can field. Theirs is this: what the deal LOOKS like from across
the table. A trade only happens where those two disagree in our favour, and
optimising the first while ignoring the second produces a long list of offers
nobody will ever accept.

WHERE THE DISAGREEMENT COMES FROM

Two places, and they are different in kind.

  DRAFT CAPITAL. Managers price players near where they drafted them, for
  months. A third-round pick who is now the 60th-best player in the league is
  still "my third-rounder" to the man who took him. This is measurable: fit
  value against ADP rank across the board and the residual IS the market's
  mistake, in points.

  RECENCY. The room overreacts to the last few weeks. `models.inseason`
  measured how much weight recent games actually deserve -- 24% after one, 39%
  after two, 57% after four. Managers apply more than that. So the same
  arithmetic run at an inflated weight reproduces what they believe, and the
  gap between their number and the honest one is buy-low and sell-high,
  falling out of the same equation rather than a rule of thumb.

ONE HONEST CAVEAT

Everything else in this project is measured. `OVERREACTION` is not -- it would
take a corpus of real trade offers to fit, which we do not have. It is an
assumption with a plausible value, exposed as a constant so it can be argued
with, and set conservatively: at 1.5 the room is credited with being wrong,
but not stupid. Nothing else here depends on it being exactly right, and
`suggest` reports both valuations so the assumption is always visible.
"""

from __future__ import annotations

import numpy as np
import polars as pl

# How much more weight the room puts on recent games than they deserve.
# 1.0 would mean the market is perfectly calibrated and there is no edge at
# all. See the caveat above: this is the one assumed constant in the project.
OVERREACTION = 1.5

# Below this many points of disagreement, a trade is noise dressed as an edge.
MIN_EDGE = 8.0

# Past this ADP rank the curve is fitting noise, and it shows: the first
# version reported British Brooks, Jerome Ford and Dameon Pierce as the most
# underpaid players in football, and a row of college quarterbacks as the most
# overpaid. Nobody drafts any of them, so their "ADP" is a placeholder rather
# than a price, and a residual against a placeholder means nothing.
#
# Same trap as the ESPN rank-vs-ADP finding: deep ADP saturates and stops
# carrying information. Beyond here the edge is reported as zero rather than
# as a number that looks meaningful and is not.
MAX_PRICED_RANK = 220.0


# What the trade market pays over what a lineup actually needs, by position.
#
# VOR already prices scarcity correctly FOR A LINEUP -- that is the whole job
# of a replacement level. This is a different claim and belongs only on the
# perceived side: managers pay a premium for running backs beyond what their
# lineup value justifies, less for receivers, least for tight ends. It is the
# oldest bias in the game and it is why "sell an RB, buy a WR" is a standing
# strategy.
#
# Applied to PERCEIVED value only. Putting it in true value would corrupt the
# lineup simulation with a market opinion, and the entire point of having two
# scales is that one of them is not an opinion.
POSITION_PREMIUM: dict[str, float] = {
    "RB": 1.12,
    "WR": 1.00,
    "TE": 0.92,
    "QB": 0.88,
}


def value_curve(board: pl.DataFrame, rank_col: str = "ecr") -> pl.DataFrame:
    """Add `market_value`: what a player at this ADP is normally worth.

    Fitted, not assumed -- a rolling median of actual value against draft rank.
    A player whose real value sits above the curve at his own ADP is one the
    room is underpaying for, and the residual is that mispricing in points.
    """
    b = board.filter(pl.col(rank_col).is_not_null() & pl.col("vor").is_not_null())
    if b.height < 20:
        return board.with_columns([
            pl.col("vor").alias("market_value"),
            pl.lit(0.0).alias("market_edge"),
        ])

    # PER POSITION, and it has to be. Value at a given ADP is not comparable
    # across positions: a quarterback at ADP 210 is SUPPOSED to have a far
    # worse VOR than a receiver at 210, because quarterback replacement level
    # is so high that most of them are worthless in this scoring. Fitting one
    # curve across everyone reported that structural fact as a mispricing, and
    # named five rookie quarterbacks as the most overpriced players in
    # football. They were priced exactly right; the curve was wrong.
    #
    # Within a position the comparison is honest: of the tight ends going
    # around pick 150, is this one better than the others?
    frames = []
    for pos in b["position"].unique().to_list():
        d = b.filter(pl.col("position") == pos).sort(rank_col)
        if d.height < 6:
            frames.append(d.select(["player_id"]).with_columns(
                pl.col("player_id").alias("player_id"),
                pl.Series("market_value", d["vor"].to_numpy().astype(float))))
            continue
        ranks = d[rank_col].to_numpy().astype(float)
        vals = d["vor"].to_numpy().astype(float)

        # Rolling median over a window that widens with rank: the top of the
        # board is dense and precise, the tail is sparse and noisy.
        fitted = np.empty_like(vals)
        for i in range(len(vals)):
            half = max(3, int(0.12 * ranks[i]))
            lo, hi = max(0, i - half), min(len(vals), i + half + 1)
            fitted[i] = np.median(vals[lo:hi])
        frames.append(d.select(["player_id"]).with_columns(
            pl.Series("market_value", fitted)))

    curve = pl.concat(frames, how="vertical")
    return (
        board.join(curve.select(["player_id", "market_value"]),
                   on="player_id", how="left")
        .with_columns(
            pl.col("market_value").fill_null(pl.col("vor")).alias("market_value")
        )
        .with_columns(
            # Only where ADP is a real price. Past MAX_PRICED_RANK the residual
            # is fitted against a placeholder, so it is zeroed rather than
            # dressed up as an edge.
            pl.when(pl.col(rank_col).fill_null(9_999) <= MAX_PRICED_RANK)
            .then(pl.col("vor") - pl.col("market_value"))
            .otherwise(pl.lit(0.0))
            .alias("market_edge")
        )
    )


def _floor(expr: pl.Expr) -> pl.Expr:
    """NOBODY IS WORTH LESS THAN NOTHING IN A TRADE.

    `market_value` is fitted on VOR, so a man below replacement level carries a
    negative one -- correct for a lineup, wrong for a negotiation. Summed across
    a package it says the other manager GAINS by handing you two useful bench
    players in exchange for your worst, because he sheds two negative numbers
    and takes on one. The scan proposed exactly that: Tyler Allgeier for Kyler
    Murray and a rookie receiver, scored as +17 in their favour.

    A player you would not roster is worth zero, not less. You can always
    decline to own him, which is the option that sets the floor.
    """
    return pl.max_horizontal(expr, pl.lit(0.0))


def perceived(
    board: pl.DataFrame,
    observed: pl.DataFrame | None = None,
    through_week: int = 0,
) -> pl.DataFrame:
    """Add `perceived_value`: the number in the other manager's head.

    Pre-season that is draft capital -- the curve above. Once games are played
    it is draft capital plus an overweighted read of the last few weeks, which
    is what produces a seller who thinks his cold star is finished and a buyer
    who thinks three good games made someone elite.
    """
    from fantasyedge.models import inseason

    b = value_curve(board)
    premium_expr = pl.col("position").replace_strict(
        POSITION_PREMIUM, default=1.0, return_dtype=pl.Float64)
    if observed is None or not observed.height or through_week <= 0:
        return b.with_columns(
            _floor(pl.col("market_value") * premium_expr)
            .alias("perceived_value"))

    j = b.join(observed, on="player_id", how="left")

    games = pl.col("games").fill_null(0.0)
    k = pl.col("position").replace_strict(
        inseason.PRIOR_STRENGTH, default=inseason.DEFAULT_K, return_dtype=pl.Float64)

    # The honest weight, then the same curve pushed past it. Capped below 1:
    # even an overreacting manager does not entirely forget who a player was.
    # THE HONEST WEIGHT uses effective games -- a spiky record is worth fewer
    # of them. THEIRS uses the raw count, amplified. That asymmetry is the
    # whole buy-low / sell-high mechanism: a man who went 30-5-5 has moved the
    # room a lot and moved the truth very little, and the space between those
    # two numbers is the trade.
    eff = pl.col("games_eff").fill_null(games) if "games_eff" in observed.columns \
        else games
    honest = pl.when(eff > 0).then(eff / (eff + k)).otherwise(0.0)
    theirs = pl.min_horizontal(honest * OVERREACTION, pl.lit(0.95))
    theirs = pl.when(games > 0).then(
        pl.min_horizontal(games / (games + k) * OVERREACTION, pl.lit(0.95))
    ).otherwise(theirs)

    weeks_left = max(inseason.MAX_WEEKS - through_week, 1)
    prior_rate = pl.col("projected_points") / pl.col("expected_games").clip(1.0, None)
    obs_rate = pl.col("ppg").fill_null(prior_rate)

    # Their rest-of-season number, then expressed on the same scale as
    # market_value by carrying across the difference from the honest rate.
    their_rate = (1 - theirs) * prior_rate + theirs * obs_rate
    honest_rate = (1 - honest) * prior_rate + honest * obs_rate
    shift = (their_rate - honest_rate) * weeks_left

    premium = pl.col("position").replace_strict(
        POSITION_PREMIUM, default=1.0, return_dtype=pl.Float64)

    return j.with_columns(
        _floor((pl.col("market_value") + shift.fill_null(0.0)) * premium)
        .alias("perceived_value")
    ).drop([c for c in ("games", "ppg", "opp_ppg", "sd", "best3", "cv", "games_eff")
            if c in j.columns])
