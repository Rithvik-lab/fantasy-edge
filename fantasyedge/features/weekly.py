"""Week-grain feature table — one row per player-week.

WHY THIS EXISTS

The season-grain risk model trains on ~6,500 rows whose labels are summary
statistics: a player's own q20/q50/q80 across his season. That throws the
weekly detail away before the model ever sees it, and volatility *is* a weekly
phenomenon.

At week grain there are well over 100,000 rows. Block C established that
sample size is the binding constraint in this project -- widening the training
window bought 3.5% while adding fourteen features bought 0.4% -- so a 15x
increase in rows is the largest lever available.

It also unlocks context that only exists weekly: opponent, rest, venue,
weather.

LEAKAGE

Every feature for week N must come from weeks 1..N-1. The pattern throughout
is shift(1) before any rolling window, so the current week is never inside its
own feature. `assert_no_leakage` checks this on real rows rather than trusting
the code to be right.

VARIANCE DECOMPOSITION

Two players with identical weekly standard deviation can be volatile for
opposite reasons:

    role       his snaps and targets swing week to week. This persists --
               it is a real property of how the offense uses him.
    efficiency steady eight targets a week, but touchdowns arrive in clumps.
               This mostly regresses; TD rate is the least sticky thing in
               football.

Same number, opposite forecast. Splitting them lets the model tell a genuinely
erratic role apart from a stable role with noisy finishing.
"""

from __future__ import annotations

import polars as pl

from fantasyedge import config
from fantasyedge.data import pull, stadiums

ROLL_SHORT = 3
ROLL_LONG = 6


def _base(seasons: list[int]) -> pl.DataFrame:
    """Regular-season weekly rows for modelled positions, sorted for windows."""
    cols = [
        "player_id", "player_name", "position", "season", "week", "team",
        "fantasy_points_ppr", "targets", "receptions", "carries",
        "receiving_yards", "rushing_yards", "receiving_tds", "rushing_tds",
        "passing_tds", "passing_yards", "attempts", "completions",
        "target_share", "air_yards_share", "receiving_air_yards",
    ]
    df = pull.load("player_stats")
    keep = [c for c in cols if c in df.columns]

    return (
        df.filter(
            pl.col("season").is_in(seasons)
            & (pl.col("season_type") == "REG")
            & pl.col("position").is_in(list(config.MODELED_POSITIONS))
            & pl.col("player_id").is_not_null()
        )
        .select(keep)
        .sort(["player_id", "season", "week"])
    )


def _opportunity(df: pl.DataFrame) -> pl.DataFrame:
    """Touches plus targets — the denominator for efficiency."""
    return df.with_columns(
        (pl.col("targets").fill_null(0)
         + pl.col("carries").fill_null(0)
         + pl.col("attempts").fill_null(0) * 0.5).alias("opportunity")
    ).with_columns(
        pl.when(pl.col("opportunity") > 0)
        .then(pl.col("fantasy_points_ppr") / pl.col("opportunity"))
        .otherwise(None)
        .alias("points_per_opp")
    )


def _lagged(col: str, window: int, fn: str = "mean") -> pl.Expr:
    """Rolling stat over the weeks *before* this one.

    shift(1) first so the current week can never enter its own feature.
    """
    shifted = pl.col(col).shift(1).over(["player_id", "season"])
    roll = getattr(shifted, f"rolling_{fn}")(window_size=window, min_samples=2)
    return roll.over(["player_id", "season"])


def _expanding(col: str, fn: str = "mean") -> pl.Expr:
    """Season-to-date stat through the previous week."""
    shifted = pl.col(col).shift(1).over(["player_id", "season"])
    roll = getattr(shifted, f"cum_{fn}")()
    return roll.over(["player_id", "season"])


def build(seasons: list[int] | None = None) -> pl.DataFrame:
    """Player-week rows with leakage-safe rolling features."""
    if seasons is None:
        seasons = list(range(config.RAW_SEASON_START, config.RAW_SEASON_END + 1))

    df = _opportunity(_base(seasons))

    df = df.with_columns([
        # --- recent form -------------------------------------------------
        _lagged("fantasy_points_ppr", ROLL_SHORT).alias("pts_l3"),
        _lagged("fantasy_points_ppr", ROLL_LONG).alias("pts_l6"),
        _lagged("fantasy_points_ppr", ROLL_LONG, "std").alias("pts_sd_l6"),
        _lagged("fantasy_points_ppr", ROLL_LONG, "min").alias("pts_min_l6"),
        _lagged("fantasy_points_ppr", ROLL_LONG, "max").alias("pts_max_l6"),

        # --- role: how much work, and how steadily -----------------------
        _lagged("opportunity", ROLL_SHORT).alias("opp_l3"),
        _lagged("opportunity", ROLL_LONG).alias("opp_l6"),
        _lagged("target_share", ROLL_LONG).alias("tgt_share_l6"),
        _lagged("targets", ROLL_LONG).alias("targets_l6"),
        _lagged("carries", ROLL_LONG).alias("carries_l6"),

        # ROLE VOLATILITY -- swing in workload itself. Persists.
        _lagged("opportunity", ROLL_LONG, "std").alias("role_volatility"),
        _lagged("target_share", ROLL_LONG, "std").alias("tgt_share_volatility"),

        # EFFICIENCY VOLATILITY -- swing in points per opportunity, i.e.
        # finishing. Mostly regresses.
        _lagged("points_per_opp", ROLL_LONG, "std").alias("efficiency_volatility"),
        _lagged("points_per_opp", ROLL_LONG).alias("points_per_opp_l6"),

        # --- season to date ----------------------------------------------
        _expanding("fantasy_points_ppr", "sum").alias("pts_std"),
        _expanding("opportunity", "sum").alias("opp_std"),
        (pl.col("week") - 1).alias("weeks_played_prior"),
    ])

    # Consistency framed the way a manager actually experiences it.
    df = df.with_columns([
        pl.when(pl.col("pts_l6") > 0)
        .then(pl.col("pts_sd_l6") / pl.col("pts_l6"))
        .otherwise(None)
        .alias("coef_variation_l6"),

        (pl.col("pts_max_l6") - pl.col("pts_min_l6")).alias("pts_range_l6"),

        # What share of a player's variance is workload rather than
        # finishing. High = genuinely erratic role. Low = steady role, noisy
        # touchdowns, and the noise should regress.
        pl.when(
            (pl.col("role_volatility").is_not_null())
            & (pl.col("efficiency_volatility").is_not_null())
            & ((pl.col("role_volatility") + pl.col("efficiency_volatility")) > 0)
        )
        .then(pl.col("role_volatility")
              / (pl.col("role_volatility") + pl.col("efficiency_volatility")))
        .otherwise(None)
        .alias("role_share_of_volatility"),
    ])

    return _add_context(df)


def _add_context(df: pl.DataFrame) -> pl.DataFrame:
    """Opponent, venue, rest and Vegas line — knowable before kickoff."""
    sched = pull.load("schedules")
    keep = [c for c in ("season", "week", "home_team", "away_team", "roof",
                        "surface", "spread_line", "total_line") if c in sched.columns]
    s = sched.select(keep)

    home = s.rename({"home_team": "team", "away_team": "opponent"}).with_columns(
        pl.lit(True).alias("is_home")
    )
    away = s.rename({"away_team": "team", "home_team": "opponent"}).with_columns([
        pl.lit(False).alias("is_home"),
        (pl.col("spread_line") * -1).alias("spread_line"),
    ])
    games = pl.concat([home, away], how="diagonal")

    out = df.join(games, on=["season", "week", "team"], how="left")

    # Implied team total: the market's expectation for this offence, and the
    # best single game-script feature available.
    if "total_line" in out.columns and "spread_line" in out.columns:
        out = out.with_columns(
            (pl.col("total_line") / 2 - pl.col("spread_line") / 2)
            .alias("implied_team_total")
        )

    st = stadiums.frame().select(["team", "is_indoor", "is_cold_weather"])
    return out.join(
        st.rename({"team": "opponent"}), on="opponent", how="left"
    )


LABEL = "fantasy_points_ppr"

EXCLUDE_FROM_FEATURES = {
    "player_id", "player_name", "position", "team", "opponent",
    "season", "week", LABEL,
    # same-week outcomes — these are the label in disguise
    "targets", "receptions", "carries", "receiving_yards", "rushing_yards",
    "receiving_tds", "rushing_tds", "passing_tds", "passing_yards",
    "attempts", "completions", "target_share", "air_yards_share",
    "receiving_air_yards", "opportunity", "points_per_opp",
    "roof", "surface",
}


def feature_columns(df: pl.DataFrame) -> list[str]:
    return [
        c for c in df.columns
        if c not in EXCLUDE_FROM_FEATURES
        and df[c].dtype in (pl.Float64, pl.Float32, pl.Int64, pl.Int32,
                            pl.UInt32, pl.Boolean)
    ]


def trainable(df: pl.DataFrame, min_prior_weeks: int = 3) -> pl.DataFrame:
    """Rows with enough prior weeks for the rolling features to mean anything."""
    return df.filter(
        pl.col("weeks_played_prior").ge(min_prior_weeks)
        & pl.col(LABEL).is_not_null()
        & pl.col("pts_l6").is_not_null()
    )


def assert_no_leakage(df: pl.DataFrame, n: int = 500) -> dict:
    """Verify rolling features exclude the current week, on real rows.

    Checks that pts_l3 equals the mean of the actual previous three weeks.
    Cheaper to run than to reason about, and it has caught real bugs.
    """
    sample = (
        df.filter(pl.col("week") >= 5)
        .sort(["player_id", "season", "week"])
        .head(n)
    )
    recomputed = (
        df.sort(["player_id", "season", "week"])
        .with_columns(
            pl.col(LABEL).shift(1).rolling_mean(window_size=3, min_samples=2)
            .over(["player_id", "season"]).alias("_check")
        )
        .select(["player_id", "season", "week", "_check"])
    )
    j = sample.join(recomputed, on=["player_id", "season", "week"], how="inner")
    bad = j.filter(
        (pl.col("pts_l3") - pl.col("_check")).abs() > 1e-6
    ).height
    return {"checked": j.height, "mismatches": bad,
            "verdict": "PASS" if bad == 0 else "FAIL"}


def save(df: pl.DataFrame) -> None:
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    df.write_parquet(config.PROCESSED / "weekly_features.parquet")


def load() -> pl.DataFrame:
    path = config.PROCESSED / "weekly_features.parquet"
    if not path.exists():
        raise FileNotFoundError("run scripts/build_weekly.py")
    return pl.read_parquet(path)
