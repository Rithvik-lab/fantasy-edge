"""Repricing a player once the season has started.

THE PROBLEM

August says a player is worth 14 points a game. Week 3 says he has scored 21,
9, and 24. Which do you believe? Believe the season entirely and you are
trading on three games of noise -- a back who found the end zone twice in week
one is not suddenly elite. Believe August entirely and you never notice that a
rookie won the job, or that the starter ahead of him is finished.

The answer is neither: weight them, and let the weight GROW with evidence.

    revised rate = (1 - w) * preseason + w * observed
    w = n / (n + k)

`k` is prior strength in games -- how much evidence it takes to move halfway
off the pre-season number. It is MEASURED per position, not chosen, by fitting

    rest_of_season_ppg ~ a * preseason + b * observed_first_n

on 2002-2023 and reading the weight off b / (a + b). Fitted weights:

    games seen     1     2     3     4     5     6
    RB          0.32  0.48  0.55  0.65  0.71  0.80
    WR          0.23  0.38  0.47  0.55  0.63  0.69
    TE          0.19  0.30  0.41  0.48  0.53  0.64
    QB          0.23  0.33  0.44  0.53  0.54  0.66

which the single-parameter form above reproduces closely. The ordering is
football, not statistics: a running back's role is close to binary and changes
fast, so his early games mean the most; tight end scoring leans hardest on
touchdowns, which is the noisiest thing on the field, so his mean the least.

WHY POINTS AND NOT OPPORTUNITY

The received wisdom -- volume is sticky, efficiency is noise, so update on
touches -- does not survive contact with the data here. Per position, given a
prior, R^2 of rest-of-season ppg:

                 prior   +points   +touches   +both
    RB           0.393     0.588      0.538   0.597
    WR           0.480     0.619      0.503   0.619
    TE           0.430     0.555      0.472   0.555
    QB           0.238     0.391      0.279   0.392

Points beat touches at every position, and touches add nothing on top of points
anywhere except running back, where they are worth a consistent point of R^2
across every window tested. So RB carries a small opportunity term and nobody
else does. An earlier pooled test appeared to say the opposite; it was pooling
positions and using carries, which are meaningless for a receiver.

WHAT THIS DELIBERATELY DOES NOT DO

It does not touch availability. A player who left in the first quarter did not
have a bad game, he had an injury, and averaging that 2.1 into his rate would
corrupt the one number this is trying to estimate. Games missed and partial
games belong to expected_games and to `fantasyedge.data.depth` -- not here.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasyedge import config

# Prior strength in games, measured. See the module docstring for the fit.
PRIOR_STRENGTH: dict[str, float] = {
    "RB": 2.1,
    "WR": 3.3,
    "TE": 4.3,
    "QB": 3.7,
}
# Kickers and defences are almost pure noise week to week; hold them near the
# pre-season number rather than chasing a two-week hot streak.
DEFAULT_K = 6.0

# The only position where touches carry information the points do not.
OPPORTUNITY_POSITIONS = frozenset({"RB"})
OPPORTUNITY_SHARE = 0.25   # of the observed side, the rest being points

MAX_WEEKS = 17


def weight(position: str, games: float) -> float:
    """How much of the revised rate comes from what has actually happened."""
    if games <= 0:
        return 0.0
    k = PRIOR_STRENGTH.get(position, DEFAULT_K)
    return float(games / (games + k))


def reprice(
    board: pl.DataFrame,
    observed: pl.DataFrame,
    through_week: int,
) -> pl.DataFrame:
    """Rest-of-season projection, given what the season has shown so far.

    `board` is the pre-season board. `observed` needs player_id, games, ppg and
    optionally opp_ppg (points-equivalent from touches, for running backs).

    Returns the board with `projected_points` replaced by a REST-OF-SEASON
    number and the season band rescaled to match. The band also narrows, by
    sqrt(k / (k + n)) -- the posterior on a rate is tighter than the prior, and
    a trade in week 8 is a better-understood bet than the same trade in August.
    """
    weeks_left = max(MAX_WEEKS - through_week, 0)
    if weeks_left == 0:
        return board.with_columns(pl.lit(0.0).alias("projected_points"))

    b = board.join(observed, on="player_id", how="left")

    games = pl.col("games").fill_null(0.0)
    k = pl.col("position").replace_strict(
        PRIOR_STRENGTH, default=DEFAULT_K, return_dtype=pl.Float64)
    w = pl.when(games > 0).then(games / (games + k)).otherwise(0.0)

    # Pre-season rate, per game he was expected to play.
    prior_rate = pl.col("projected_points") / pl.col("expected_games").clip(1.0, None)

    # Observed side: points, blended with a touch-based estimate for backs.
    obs_rate = pl.col("ppg")
    if "opp_ppg" in observed.columns:
        obs_rate = (
            pl.when(pl.col("position").is_in(list(OPPORTUNITY_POSITIONS))
                    & pl.col("opp_ppg").is_not_null())
            .then((1 - OPPORTUNITY_SHARE) * pl.col("ppg")
                  + OPPORTUNITY_SHARE * pl.col("opp_ppg"))
            .otherwise(pl.col("ppg"))
        )

    revised = (1 - w) * prior_rate + w * obs_rate.fill_null(prior_rate)

    # Games he is likely to play in what is left, at his established rate of
    # availability -- not a flat "all of them".
    avail = (pl.col("expected_games") / MAX_WEEKS).clip(0.05, 1.0)
    games_left = pl.lit(float(weeks_left)) * avail

    # Rate uncertainty shrinks with evidence; the band scales with both that
    # and how much season is left to accumulate in.
    narrow = (k / (k + games)).sqrt()
    span = pl.lit(float(weeks_left) / MAX_WEEKS)

    out = b.with_columns([
        revised.alias("_rate"),
        w.alias("update_weight"),
        games_left.alias("_games_left"),
    ]).with_columns([
        (pl.col("_rate") * pl.col("_games_left")).alias("ros_points"),
    ])

    # Rescale the season band around the new centre, keeping its shape.
    for q in ("season_p20", "season_p50", "season_p80"):
        if q in out.columns:
            centre = pl.col("ros_points")
            offset = (pl.col(q) - pl.col("season_p50")) * span * narrow
            out = out.with_columns((centre + offset).alias(q))

    return (
        out.with_columns([
            pl.col("ros_points").alias("projected_points"),
            pl.col("_games_left").alias("expected_games"),
            (pl.col("season_p80") - pl.col("season_p20")).alias("season_range"),
        ])
        .drop([c for c in ("_rate", "_games_left", "games", "ppg", "opp_ppg",
                           "ros_points") if c in out.columns])
    )


def observe(weekly: pl.DataFrame, season: int, through_week: int) -> pl.DataFrame:
    """Roll weekly box scores up into what `reprice` needs.

    Weeks a player did not play are excluded rather than counted as zero. A
    missed game is availability, not performance, and folding it into the rate
    would double-count it -- expected_games already carries it.
    """
    d = weekly.filter(
        (pl.col("season") == season) & (pl.col("week") <= through_week)
    )
    if "fantasy_points_ppr" not in d.columns:
        return pl.DataFrame()

    agg = [
        # Float64, not the UInt32 `len` hands back. Nothing here subtracts two
        # counts, but this project has shipped five separate underflow bugs
        # from exactly this dtype and the cast costs nothing.
        pl.len().cast(pl.Float64).alias("games"),
        pl.col("fantasy_points_ppr").mean().alias("ppg"),
    ]
    if "opportunity" in d.columns and "points_per_opp" in d.columns:
        # Touches priced at the player's own efficiency: what his volume would
        # be worth if his luck were merely typical for him.
        agg.append(
            (pl.col("opportunity").mean() * pl.col("points_per_opp").median())
            .alias("opp_ppg")
        )

    return d.group_by("player_id").agg(agg)


def measure(seasons: tuple[int, int] = (2002, 2023)) -> dict:
    """Re-derive PRIOR_STRENGTH from the data. The constants above came from here.

    Deliberately excludes the sealed checkpoint seasons -- no model constant in
    this project is allowed to be chosen against them.
    """
    path = config.PROCESSED / "weekly_features.parquet"
    if not path.exists():
        return {"error": "weekly_features.parquet not built"}

    w = pl.read_parquet(path).filter(
        pl.col("season").is_between(*seasons)
    ).select(["player_id", "season", "week", "position", "fantasy_points_ppr"]
             ).drop_nulls()

    prior = (w.group_by(["player_id", "season"])
              .agg(pl.col("fantasy_points_ppr").mean().alias("prior"),
                   pl.len().alias("g"))
              .filter(pl.col("g") >= 8)
              .with_columns((pl.col("season") + 1).alias("season")))

    out: dict[str, dict] = {}
    for pos in PRIOR_STRENGTH:
        dp = w.filter(pl.col("position") == pos)
        ws, ks = {}, []
        for n in (1, 2, 3, 4, 5, 6):
            obs = (dp.filter(pl.col("week") <= n)
                     .group_by(["player_id", "season"])
                     .agg(pl.col("fantasy_points_ppr").mean().alias("o"),
                          pl.len().alias("na"))
                     .filter(pl.col("na") == n))
            rest = (dp.filter(pl.col("week") > n)
                      .group_by(["player_id", "season"])
                      .agg(pl.col("fantasy_points_ppr").mean().alias("y"),
                           pl.len().alias("nb"))
                      .filter(pl.col("nb") >= 5))
            j = (obs.join(rest, on=["player_id", "season"])
                    .join(prior.select(["player_id", "season", "prior"]),
                          on=["player_id", "season"]))
            if j.height < 150:
                continue
            y = j["y"].to_numpy()
            X = np.column_stack([np.ones(j.height), j["prior"].to_numpy(),
                                 j["o"].to_numpy()])
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            a, bb = beta[1], beta[2]
            if a + bb <= 0:
                continue
            sh = bb / (a + bb)
            ws[n] = round(float(sh), 3)
            if 0 < sh < 1:
                ks.append(n * (1 - sh) / sh)
        out[pos] = {"weights": ws,
                    "k": round(float(np.median(ks)), 2) if ks else None,
                    "current": PRIOR_STRENGTH[pos]}
    return out
