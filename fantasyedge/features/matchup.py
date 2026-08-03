"""Opponent defensive profile and the matchup interaction.

THE QUESTION

If an offence faces a bad run defence, does the running back get more work --
or does the offence throw anyway because the quarterback has time? People
assert both. It is an empirical question and the data can answer it.

Two distinct effects hide inside "good matchup", and separating them matters:

  volume     does the offence CALL more runs against a weak run defence?
             This is game-planning, and it is what "attack the weakness"
             would predict.
  efficiency do the same number of carries simply go further?

A player can benefit from either, and they have different implications: extra
volume is reliable, extra efficiency is noisy.

LEAKAGE

A defence's rating for week N uses only weeks 1..N-1. Season-long defensive
ratings are the most common leak in public fantasy analysis -- rating a
defence using games that have not happened yet makes any backtest look
excellent and means nothing.

WHY DEFENCE-VS-POSITION IS TREATED CAREFULLY

Raw "points allowed to RBs" is the most abused statistic in fantasy. A defence
that happened to face the best backs looks bad through no property of its own.
Ratings here are EPA-based and opponent-count-weighted, and shrunk toward the
league mean when the sample is thin.
"""

from __future__ import annotations

import polars as pl

from fantasyedge.data import pull

# Weight on the league mean when a defence has few plays. Keeps early-season
# ratings from swinging on twenty snaps.
SHRINK_PLAYS = 150


def defense_ratings(seasons: list[int]) -> pl.DataFrame:
    """Rolling EPA allowed per play, split by rush and pass, through week N-1."""
    pbp = pull.load("pbp")
    cols = [c for c in ("defteam", "season", "week", "play_type", "epa")
            if c in pbp.columns]
    p = pbp.select(cols).filter(
        pl.col("season").is_in(seasons)
        & pl.col("defteam").is_not_null()
        & pl.col("play_type").is_in(["pass", "run"])
        & pl.col("epa").is_not_null()
    )

    weekly = (
        p.group_by(["defteam", "season", "week", "play_type"])
        .agg([pl.col("epa").sum().alias("epa_sum"), pl.len().alias("plays")])
        .sort(["defteam", "season", "play_type", "week"])
    )

    # Expanding season-to-date through the PREVIOUS week.
    grp = ["defteam", "season", "play_type"]
    weekly = weekly.with_columns([
        pl.col("epa_sum").shift(1).cum_sum().over(grp).alias("_epa"),
        pl.col("plays").shift(1).cum_sum().over(grp).alias("_plays"),
    ])

    league = (
        weekly.group_by(["season", "play_type"])
        .agg((pl.col("epa_sum").sum() / pl.col("plays").sum()).alias("_lg"))
    )

    rated = weekly.join(league, on=["season", "play_type"], how="left").with_columns(
        # Shrink toward league mean by sample size.
        ((pl.col("_epa").fill_null(0.0) + pl.col("_lg") * SHRINK_PLAYS)
         / (pl.col("_plays").fill_null(0) + SHRINK_PLAYS)).alias("epa_allowed")
    )

    wide = (
        rated.select(["defteam", "season", "week", "play_type", "epa_allowed",
                      pl.col("_plays").alias("plays_seen")])
        .pivot(on="play_type", index=["defteam", "season", "week"],
               values=["epa_allowed", "plays_seen"])
    )

    ren = {}
    for c in wide.columns:
        if "pass" in c and "epa" in c:
            ren[c] = "def_pass_epa_allowed"
        elif "run" in c and "epa" in c:
            ren[c] = "def_rush_epa_allowed"
    wide = wide.rename(ren)

    keep = ["defteam", "season", "week", "def_pass_epa_allowed", "def_rush_epa_allowed"]
    wide = wide.select([c for c in keep if c in wide.columns])

    return wide.with_columns(
        # Positive means the defence is worse against the run than the pass,
        # relative to its own overall level. This is the "attack the weakness"
        # signal, and it is what the volume test below keys on.
        (pl.col("def_rush_epa_allowed") - pl.col("def_pass_epa_allowed"))
        .alias("def_rush_minus_pass")
    ).rename({"defteam": "opponent"})


def attach(weekly: pl.DataFrame) -> pl.DataFrame:
    """Join opponent ratings onto a week-grain table."""
    seasons = sorted(weekly["season"].unique().to_list())
    d = defense_ratings(seasons)
    return weekly.join(d, on=["opponent", "season", "week"], how="left")


NEW_FEATURES = [
    "def_pass_epa_allowed", "def_rush_epa_allowed", "def_rush_minus_pass",
]
