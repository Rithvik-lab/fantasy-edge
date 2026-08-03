"""Snap share, Next Gen Stats, and PFR advanced — at week grain.

These sources were pulled on day one and never joined to the weekly table,
which was an omission rather than a decision. Snap share in particular is the
purest measure of opportunity available, and everything the project has
learned says opportunity is the dominant signal.

All three are OUTCOMES of the week they describe -- how many snaps he played,
how much separation he got. So every column is lagged: rolling windows over
weeks 1..N-1 only, never including the week being predicted.

Coverage differs and that matters for the training window:

    snap_counts    2013+   joins via pfr_id
    ngs            2016+   joins via gsis_id
    pfr_advstats   2018+   joins via pfr_id

Rows before a source starts get nulls, which XGBoost handles natively. The
risk is that "missing" correlates with era, so a feature can teach the model
about time rather than football -- worth watching in the importances.
"""

from __future__ import annotations

import polars as pl

from fantasyedge.data import crosswalk as cw
from fantasyedge.data import pull

ROLL = 6
SHORT = 3


def _roll(col: str, window: int, fn: str = "mean") -> pl.Expr:
    """Rolling stat over prior weeks only."""
    shifted = pl.col(col).shift(1).over(["player_id", "season"])
    return getattr(shifted, f"rolling_{fn}")(
        window_size=window, min_samples=2
    ).over(["player_id", "season"])


def snap_features(seasons: list[int]) -> pl.DataFrame:
    """Weekly snap share, keyed to gsis_id through the crosswalk."""
    x = cw.load().select(["gsis_id", "pfr_id"]).filter(pl.col("pfr_id").is_not_null())

    return (
        pull.load("snap_counts")
        .filter(pl.col("season").is_in(seasons) & (pl.col("game_type") == "REG"))
        .join(x, left_on="pfr_player_id", right_on="pfr_id", how="inner")
        .select([
            pl.col("gsis_id").alias("player_id"), "season", "week",
            pl.col("offense_pct").alias("_snap_pct"),
            pl.col("offense_snaps").alias("_snaps"),
        ])
        .unique(subset=["player_id", "season", "week"], keep="first")
    )


def ngs_features(seasons: list[int]) -> pl.DataFrame:
    """Next Gen receiving and rushing tracking metrics.

    Separation and cushion are talent signals independent of volume, which is
    different information from everything else in the table -- the rest keys
    on how much work a player got, not how well he creates space.
    """
    frames = []

    rec = pull.load("nextgen_stats_receiving")
    id_col = "player_gsis_id" if "player_gsis_id" in rec.columns else None
    if id_col:
        want = [c for c in ("avg_separation", "avg_cushion", "avg_yac",
                            "avg_intended_air_yards", "catch_percentage",
                            "percent_share_of_intended_air_yards")
                if c in rec.columns]
        frames.append(
            rec.filter(pl.col("season").is_in(seasons) & (pl.col("week") > 0))
            .select([pl.col(id_col).alias("player_id"), "season", "week"]
                    + [pl.col(c).alias(f"_ngs_{c}") for c in want])
        )

    rush = pull.load("nextgen_stats_rushing")
    id_col = "player_gsis_id" if "player_gsis_id" in rush.columns else None
    if id_col:
        want = [c for c in ("efficiency", "avg_time_to_los",
                            "percent_attempts_gte_eight_defenders")
                if c in rush.columns]
        frames.append(
            rush.filter(pl.col("season").is_in(seasons) & (pl.col("week") > 0))
            .select([pl.col(id_col).alias("player_id"), "season", "week"]
                    + [pl.col(c).alias(f"_ngs_{c}") for c in want])
        )

    if not frames:
        return pl.DataFrame()

    out = frames[0]
    for f in frames[1:]:
        out = out.join(f, on=["player_id", "season", "week"], how="outer_coalesce")
    return out.unique(subset=["player_id", "season", "week"], keep="first")


def pfr_features(seasons: list[int]) -> pl.DataFrame:
    """PFR advanced: drops and broken tackles."""
    x = cw.load().select(["gsis_id", "pfr_id"]).filter(pl.col("pfr_id").is_not_null())
    rec = pull.load("pfr_advstats_rec")
    want = [c for c in ("receiving_broken_tackles", "receiving_drop",
                        "receiving_drop_pct") if c in rec.columns]
    if not want:
        return pl.DataFrame()

    return (
        rec.filter(pl.col("season").is_in(seasons))
        .join(x, left_on="pfr_player_id", right_on="pfr_id", how="inner")
        .select([pl.col("gsis_id").alias("player_id"), "season", "week"]
                + [pl.col(c).alias(f"_pfr_{c}") for c in want])
        .unique(subset=["player_id", "season", "week"], keep="first")
    )


def attach(weekly: pl.DataFrame) -> pl.DataFrame:
    """Join all three sources and build lagged rolling features."""
    seasons = sorted(weekly["season"].unique().to_list())

    df = weekly.join(snap_features(seasons), on=["player_id", "season", "week"],
                     how="left")

    ngs = ngs_features(seasons)
    if ngs.height:
        df = df.join(ngs, on=["player_id", "season", "week"], how="left")

    pfr = pfr_features(seasons)
    if pfr.height:
        df = df.join(pfr, on=["player_id", "season", "week"], how="left")

    df = df.sort(["player_id", "season", "week"])

    exprs = [
        _roll("_snap_pct", SHORT).alias("snap_pct_l3"),
        _roll("_snap_pct", ROLL).alias("snap_pct_l6"),
        # Steadiness of role. A player whose snap share swings is a different
        # asset from one pinned at 80% every week, even at the same average.
        _roll("_snap_pct", ROLL, "std").alias("snap_pct_sd_l6"),
        _roll("_snaps", ROLL).alias("snaps_l6"),
    ]

    for c in df.columns:
        if c.startswith("_ngs_") or c.startswith("_pfr_"):
            exprs.append(_roll(c, ROLL).alias(c.lstrip("_") + "_l6"))

    df = df.with_columns(exprs)

    # Trend: is his role growing or shrinking? Level and direction are
    # different signals and the model cannot derive one from the other.
    if {"snap_pct_l3", "snap_pct_l6"}.issubset(df.columns):
        df = df.with_columns(
            (pl.col("snap_pct_l3") - pl.col("snap_pct_l6")).alias("snap_trend")
        )

    return df.drop([c for c in df.columns if c.startswith("_")])


def new_features(df: pl.DataFrame) -> list[str]:
    return [c for c in df.columns
            if c.startswith(("snap_pct", "snaps_l", "ngs_", "pfr_", "snap_trend"))]
