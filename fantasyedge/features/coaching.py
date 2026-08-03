"""Coaching staff and offensive scheme.

WHY COACH IDENTITY IS THE WRONG FEATURE

"Andy Reid" is not a useful input. What matters is what a staff *does* --
how often they pass in neutral situations, how fast they play, how much they
concentrate touches in one back. Those are measurable directly from
play-by-play, and unlike a name they generalise to a coach the model has
never seen.

So coach identity is used for exactly one thing it is uniquely good at:
detecting *discontinuity*. When a team changes head coach, last season's
usage stops predicting this season's, and every prior-season feature in the
model quietly becomes less trustworthy. That flag is what the model cannot
derive on its own.

WHAT THIS BUILDS

  scheme (measured from pbp, prior season)
    proe                 pass rate over expectation -- nflverse computes this
                         as pass_oe, adjusted for down, distance and score
    neutral_pass_rate    pass rate with the game close, stripping garbage time
    seconds_per_play     pace, which drives total plays and everyone's volume
    shotgun_rate, no_huddle_rate

  continuity (from schedules)
    coach_tenure         seasons the head coach has been with this team
    is_new_coach         first year, so prior-season usage is least reliable

  redistribution (the interesting one)
    backfield_concentration  how much of the rushing work goes to one back.
                             Some staffs run a true bellcow, others commit to
                             a committee, and that changes what a backup is
                             worth when the starter sits.
"""

from __future__ import annotations

import polars as pl

from fantasyedge.data import pull

# Game is close enough that play-calling reflects preference rather than
# desperation. Standard neutral-script definition.
NEUTRAL_SCORE_MARGIN = 8
NEUTRAL_WP = (0.20, 0.80)


def coach_table() -> pl.DataFrame:
    """Head coach per team-season, with tenure and change flags."""
    s = pull.load("schedules")
    need = {"season", "week", "home_team", "away_team", "home_coach", "away_coach"}
    if not need.issubset(set(s.columns)):
        return pl.DataFrame()

    home = s.select([
        "season", pl.col("home_team").alias("team"),
        pl.col("home_coach").alias("coach"),
    ])
    away = s.select([
        "season", pl.col("away_team").alias("team"),
        pl.col("away_coach").alias("coach"),
    ])

    # A team can have an interim coach mid-season; take the most frequent.
    per_season = (
        pl.concat([home, away])
        .filter(pl.col("coach").is_not_null() & pl.col("team").is_not_null())
        .group_by(["season", "team", "coach"])
        .agg(pl.len().alias("games"))
        .sort(["season", "team", "games"], descending=[False, False, True])
        .unique(subset=["season", "team"], keep="first")
        .drop("games")
        .sort(["team", "season"])
    )

    return per_season.with_columns([
        pl.col("coach").shift(1).over("team").alias("_prev_coach"),
    ]).with_columns([
        (pl.col("coach") != pl.col("_prev_coach")).fill_null(True).alias("is_new_coach"),
    ]).with_columns([
        # Tenure: seasons since the last change, counted within team.
        pl.col("is_new_coach").cum_sum().over("team").alias("_stint"),
    ]).with_columns([
        pl.col("season").rank("ordinal").over(["team", "_stint"])
        .cast(pl.Int32).alias("coach_tenure"),
    ]).drop(["_prev_coach", "_stint"])


def scheme_profile(seasons: list[int]) -> pl.DataFrame:
    """Per team-season offensive tendencies, measured from play-by-play."""
    pbp = pull.load("pbp")
    cols = [c for c in ("posteam", "season", "week", "play_type", "pass", "rush",
                        "pass_oe", "shotgun", "no_huddle", "score_differential",
                        "wp", "game_seconds_remaining", "rusher_player_id")
            if c in pbp.columns]
    p = pbp.select(cols).filter(
        pl.col("season").is_in(seasons)
        & pl.col("posteam").is_not_null()
        & pl.col("play_type").is_in(["pass", "run"])
    )

    neutral = p.filter(
        (pl.col("score_differential").abs() <= NEUTRAL_SCORE_MARGIN)
        & pl.col("wp").is_between(*NEUTRAL_WP)
    )

    overall = (
        p.group_by(["posteam", "season"])
        .agg([
            pl.len().alias("plays"),
            pl.col("pass").mean().alias("pass_rate"),
            pl.col("pass_oe").mean().alias("proe"),
            pl.col("shotgun").mean().alias("shotgun_rate"),
            pl.col("no_huddle").mean().alias("no_huddle_rate"),
        ])
    )

    neut = (
        neutral.group_by(["posteam", "season"])
        .agg([
            pl.col("pass").mean().alias("neutral_pass_rate"),
            pl.col("pass_oe").mean().alias("neutral_proe"),
            pl.len().alias("neutral_plays"),
        ])
    )

    # Pace: seconds burned per offensive play, from clock deltas.
    pace = (
        p.sort(["posteam", "season", "week"])
        .group_by(["posteam", "season"])
        .agg([
            pl.col("game_seconds_remaining").min().alias("_lo"),
            pl.col("game_seconds_remaining").max().alias("_hi"),
            pl.len().alias("_n"),
        ])
        .with_columns(
            pl.when(pl.col("_n") > 0)
            .then((pl.col("_hi") - pl.col("_lo")) / pl.col("_n"))
            .otherwise(None)
            .alias("seconds_per_play")
        )
        .drop(["_lo", "_hi", "_n"])
    )

    out = overall.join(neut, on=["posteam", "season"], how="left").join(
        pace, on=["posteam", "season"], how="left"
    )
    return out.rename({"posteam": "team"})


def backfield_concentration(seasons: list[int]) -> pl.DataFrame:
    """How concentrated the running game is, per team-season.

    A bellcow staff and a committee staff produce very different answers to
    "what is the backup worth if the starter sits", which is the question the
    teammate features raised and could not answer.

    Herfindahl index over carry share: 1.0 is one back taking everything,
    values near zero are a wide committee.
    """
    pbp = pull.load("pbp")
    if "rusher_player_id" not in pbp.columns:
        return pl.DataFrame()

    runs = pbp.select(
        [c for c in ("posteam", "season", "rusher_player_id") if c in pbp.columns]
    ).filter(
        pl.col("season").is_in(seasons)
        & pl.col("posteam").is_not_null()
        & pl.col("rusher_player_id").is_not_null()
    )

    by_back = (
        runs.group_by(["posteam", "season", "rusher_player_id"])
        .agg(pl.len().alias("carries"))
    )
    totals = (
        by_back.group_by(["posteam", "season"])
        .agg(pl.col("carries").sum().alias("team_carries"))
    )

    return (
        by_back.join(totals, on=["posteam", "season"])
        .with_columns((pl.col("carries") / pl.col("team_carries")).alias("share"))
        .group_by(["posteam", "season"])
        .agg([
            (pl.col("share") ** 2).sum().alias("backfield_concentration"),
            pl.col("share").max().alias("lead_back_share"),
        ])
        .rename({"posteam": "team"})
    )


def build(seasons: list[int]) -> pl.DataFrame:
    """Team-season coaching and scheme table, lagged one season.

    Scheme is measured from the *prior* season so it is knowable before the
    season being predicted. Coach continuity is known in the offseason, so it
    applies to the current season directly.
    """
    src = sorted(set(seasons) | {min(seasons) - 1})

    scheme = scheme_profile(src)
    backs = backfield_concentration(src)
    prof = scheme.join(backs, on=["team", "season"], how="left")

    # Lag scheme by one season.
    lagged = prof.with_columns((pl.col("season") + 1).alias("season")).rename({
        c: f"prior_{c}" for c in prof.columns if c not in ("team", "season")
    })

    coaches = coach_table()
    if coaches.height:
        lagged = lagged.join(coaches, on=["team", "season"], how="left")

    return lagged.filter(pl.col("season").is_in(seasons))


NEW_FEATURES = [
    "prior_proe", "prior_neutral_pass_rate", "prior_neutral_proe",
    "prior_pass_rate", "prior_shotgun_rate", "prior_no_huddle_rate",
    "prior_seconds_per_play", "prior_backfield_concentration",
    "prior_lead_back_share", "coach_tenure", "is_new_coach",
]
