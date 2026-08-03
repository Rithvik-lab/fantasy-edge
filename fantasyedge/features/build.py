"""Build the player-season feature table.

One row = one player, one season. Features describe season N-1; labels
describe season N. That lag is the whole point — it's what makes the table
usable at draft time, when season N hasn't happened yet.

    season_panel()   per-player-season stats, one row per season played
    build()          lag the panel, attach labels, return the training table

Regular season only, everywhere. Postseason games distort per-game rates and
only a third of the league plays them.
"""

from __future__ import annotations

import polars as pl

from fantasyedge import config
from fantasyedge.data import crosswalk as cw
from fantasyedge.data import pull

# --------------------------------------------------------------------------
# Per-season aggregates — the raw material for both features and labels
# --------------------------------------------------------------------------

def _weekly(seasons: list[int]) -> pl.DataFrame:
    """Regular-season weekly rows for modelled positions."""
    return (
        pull.load("player_stats")
        .filter(
            pl.col("season").is_in(seasons)
            & (pl.col("season_type") == "REG")
            & pl.col("position").is_in(list(config.MODELED_POSITIONS))
            & pl.col("player_id").is_not_null()
        )
    )


def season_panel(seasons: list[int]) -> pl.DataFrame:
    """Aggregate weekly stats to one row per player-season.

    Carries both the volume totals that become features and the distribution
    shape (quantiles, sd) that becomes the risk labels.
    """
    w = _weekly(seasons)

    pts = pl.col("fantasy_points_ppr")

    panel = (
        w.group_by(["player_id", "season"])
        .agg([
            pl.col("player_name").first().alias("player_name"),
            pl.col("position").first().alias("position"),
            pl.col("team").last().alias("team"),

            # volume
            pl.len().alias("games"),
            pts.sum().alias("total_points"),
            pts.mean().alias("ppg"),

            # distribution shape -> risk labels
            pts.std().alias("weekly_sd"),
            pts.quantile(0.20).alias("weekly_q20"),
            pts.quantile(0.50).alias("weekly_q50"),
            pts.quantile(0.80).alias("weekly_q80"),

            # opportunity
            pl.col("targets").sum().alias("targets"),
            pl.col("receptions").sum().alias("receptions"),
            pl.col("carries").sum().alias("carries"),
            pl.col("target_share").mean().alias("target_share"),
            pl.col("air_yards_share").mean().alias("air_yards_share"),
            pl.col("receiving_air_yards").sum().alias("air_yards"),

            # usage consistency -> a volatility feature, computed from
            # week-to-week variation in role rather than in output
            pl.col("target_share").std().alias("target_share_sd"),

            # scoring mix
            (pl.col("receiving_tds").sum() + pl.col("rushing_tds").sum()
             + pl.col("passing_tds").sum()).alias("total_tds"),
            pl.col("receiving_yards").sum().alias("rec_yards"),
            pl.col("rushing_yards").sum().alias("rush_yards"),
            pl.col("rushing_tds").sum().alias("rush_tds"),
            pl.col("receiving_tds").sum().alias("rec_tds"),

            # Passing. Without these a QB is projected from his carries and
            # his (roughly zero) targets, which is why QB error and QB risk
            # discrimination were both the worst of any position.
            pl.col("attempts").sum().alias("pass_attempts"),
            pl.col("completions").sum().alias("completions"),
            pl.col("passing_yards").sum().alias("pass_yards"),
            pl.col("passing_tds").sum().alias("pass_tds"),
            pl.col("passing_interceptions").sum().alias("interceptions"),
            pl.col("passing_epa").mean().alias("passing_epa"),
            pl.col("passing_cpoe").mean().alias("passing_cpoe"),
        ])
    )

    # polars returns counts as UInt32. Any later subtraction between two of
    # them (games - prior_games, team_games - games_played) underflows to
    # 2**32 instead of going negative. Cast once, here, so nothing downstream
    # has to remember.
    panel = panel.with_columns([
        pl.col(c).cast(pl.Int32)
        for c in ("games", "targets", "receptions", "carries", "total_tds")
        if c in panel.columns
    ])

    return panel.with_columns([
        # Share of points from touchdowns. TDs are the lumpiest and least
        # sticky scoring event — expected to be the strongest volatility driver.
        pl.when(pl.col("total_points") > 0)
        .then(pl.col("total_tds") * 6.0 / pl.col("total_points"))
        .otherwise(None)
        .alias("td_share_of_points"),

        # Share of points from receptions. In full PPR this is the floor
        # mechanism: catches are the most stable scoring event there is.
        pl.when(pl.col("total_points") > 0)
        .then(pl.col("receptions") * config.POINTS_PER_RECEPTION
              / pl.col("total_points"))
        .otherwise(None)
        .alias("reception_share_of_points"),

        # Average depth of target — deep threats are boom/bust by construction.
        pl.when(pl.col("targets") > 0)
        .then(pl.col("air_yards") / pl.col("targets"))
        .otherwise(None)
        .alias("adot"),

        pl.when(pl.col("games") > 0)
        .then(pl.col("targets") / pl.col("games"))
        .otherwise(None)
        .alias("targets_per_game"),

        # Where a player's points actually come from. A QB who runs and a QB
        # who only throws have very different floors and ceilings even at the
        # same total, and the same is true of a receiving back.
        pl.when(pl.col("total_points") > 0)
        .then((pl.col("rush_yards") * 0.1 + pl.col("rush_tds") * 6.0)
              / pl.col("total_points"))
        .otherwise(None)
        .alias("rush_share_of_points"),

        pl.when(pl.col("total_points") > 0)
        .then((pl.col("pass_yards") * 0.04 + pl.col("pass_tds") * 4.0)
              / pl.col("total_points"))
        .otherwise(None)
        .alias("pass_share_of_points"),

        pl.when(pl.col("games") > 0)
        .then(pl.col("pass_attempts") / pl.col("games"))
        .otherwise(None)
        .alias("attempts_per_game"),

        pl.when(pl.col("games") > 0)
        .then(pl.col("carries") / pl.col("games"))
        .otherwise(None)
        .alias("carries_per_game"),

        pl.when(pl.col("pass_attempts") > 0)
        .then(pl.col("completions") / pl.col("pass_attempts"))
        .otherwise(None)
        .alias("completion_pct"),
    ])


# --------------------------------------------------------------------------
# Joins onto the panel
# --------------------------------------------------------------------------

def snap_features(seasons: list[int]) -> pl.DataFrame:
    """Snap share per player-season, routed through the crosswalk.

    snap_counts is PFR-sourced and keyed by pfr_player_id, so this is the
    first place the ID spine actually earns its keep.
    """
    x = cw.load().select(["gsis_id", "pfr_id"]).filter(pl.col("pfr_id").is_not_null())

    snaps = (
        pull.load("snap_counts")
        .filter(pl.col("season").is_in(seasons) & (pl.col("game_type") == "REG"))
        .join(x, left_on="pfr_player_id", right_on="pfr_id", how="inner")
        .group_by(["gsis_id", "season"])
        .agg([
            pl.col("offense_pct").mean().alias("snap_pct"),
            pl.col("offense_pct").std().alias("snap_pct_sd"),
            pl.col("offense_snaps").sum().alias("snaps"),
        ])
        .rename({"gsis_id": "player_id"})
    )
    return snaps


def expected_points_features(seasons: list[int]) -> pl.DataFrame:
    """Expected fantasy points from ffopportunity.

    actual - expected is the strongest regression-to-mean signal available:
    a player who beat his opportunity is a sell, and the reverse is a sleeper.
    """
    exp_cols = ["rec_fantasy_points_exp", "rush_fantasy_points_exp",
                "pass_fantasy_points_exp"]

    # ffopportunity types season as String and week as Float, unlike every
    # other nflverse table. Cast before joining or the join silently drops
    # every row.
    op = (
        pull.load("ff_opportunity")
        .with_columns([
            pl.col("season").cast(pl.Int32, strict=False),
            pl.col("week").cast(pl.Int32, strict=False),
        ])
        .filter(pl.col("season").is_in(seasons) & (pl.col("week") <= 18))
    )
    present = [c for c in exp_cols if c in op.columns]
    if not present:
        return pl.DataFrame({"player_id": [], "season": [], "expected_points": []})

    total = pl.sum_horizontal([pl.col(c).fill_null(0.0) for c in present])
    return (
        op.with_columns(total.alias("_exp"))
        .group_by(["player_id", "season"])
        .agg(pl.col("_exp").sum().alias("expected_points"))
    )


def team_context(seasons: list[int]) -> pl.DataFrame:
    """Team pass volume and pace, derived from the weekly player rows."""
    w = _weekly(seasons)
    return (
        w.group_by(["team", "season"])
        .agg([
            pl.col("targets").sum().alias("team_targets"),
            pl.col("carries").sum().alias("team_carries"),
            pl.col("fantasy_points_ppr").sum().alias("team_fantasy_points"),
        ])
        .with_columns(
            pl.when((pl.col("team_targets") + pl.col("team_carries")) > 0)
            .then(pl.col("team_targets")
                  / (pl.col("team_targets") + pl.col("team_carries")))
            .otherwise(None)
            .alias("team_pass_rate")
        )
    )


def bio_features() -> pl.DataFrame:
    """Age, experience, and draft capital from the crosswalk."""
    x = cw.load()
    cols = ["gsis_id"] + [c for c in ("age", "draft_year", "draft_round", "draft_ovr")
                          if c in x.columns]
    return x.select(cols).rename({"gsis_id": "player_id"})


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

FEATURE_COLUMNS = [
    # volume and role
    "games", "total_points", "ppg", "weekly_sd",
    "targets", "receptions", "carries", "target_share", "air_yards_share",
    "air_yards", "target_share_sd", "total_tds", "rec_yards", "rush_yards",
    "adot", "targets_per_game", "snap_pct", "snap_pct_sd", "snaps",
    "expected_points", "points_over_expected", "team_pass_rate",
    # block A: passing
    "pass_attempts", "completions", "pass_yards", "pass_tds",
    "interceptions", "passing_epa", "passing_cpoe",
    "attempts_per_game", "completion_pct",
    # block A: scoring mix
    "td_share_of_points", "reception_share_of_points",
    "rush_share_of_points", "pass_share_of_points",
    "rush_tds", "rec_tds", "carries_per_game",
]

LABEL_COLUMNS = [
    "total_points", "ppg", "games",
    "weekly_sd", "weekly_q20", "weekly_q50", "weekly_q80",
]


def build(seasons: list[int] | None = None) -> pl.DataFrame:
    """The training table: prior-season features, current-season labels.

    A row survives only if the player has a prior season to describe him.
    Rookies have no such season and are excluded here by construction — they
    need a separate model built on draft capital and combine data.
    """
    if seasons is None:
        seasons = list(range(config.RAW_SEASON_START, config.RAW_SEASON_END + 1))

    panel = season_panel(seasons)
    panel = (
        panel
        .join(snap_features(seasons), on=["player_id", "season"], how="left")
        .join(expected_points_features(seasons), on=["player_id", "season"], how="left")
        .join(team_context(seasons), on=["team", "season"], how="left")
        .with_columns(
            (pl.col("total_points") - pl.col("expected_points"))
            .alias("points_over_expected")
        )
    )

    feats = [c for c in FEATURE_COLUMNS if c in panel.columns]

    # Features describe the prior season. Shift the season forward by one so a
    # row keyed (player, N) carries season N-1's numbers.
    prior = (
        panel.select(["player_id", "season", "position", "player_name"] + feats)
        .with_columns((pl.col("season") + 1).alias("season"))
        .rename({c: f"prior_{c}" for c in feats})
    )

    labels = panel.select(["player_id", "season", "team"] + LABEL_COLUMNS)

    table = (
        prior.join(labels, on=["player_id", "season"], how="inner")
        .join(bio_features(), on="player_id", how="left")
    )

    # Age is a point-in-time value in the crosswalk; back it out to the row's
    # season so a 2016 row doesn't carry a 2026 age.
    if "age" in table.columns:
        table = table.with_columns(
            (pl.col("age") - (config.RAW_SEASON_END + 1 - pl.col("season")))
            .alias("age")
        )

    return table.sort(["season", "player_id"])


def inference_frame(target_season: int | None = None) -> pl.DataFrame:
    """Features for a season that hasn't been played — no labels.

    build() inner-joins labels, so the target season drops out by design. This
    is the same feature construction without that join: what the production
    model consumes on draft day.
    """
    if target_season is None:
        target_season = config.PRODUCTION_TARGET_SEASON

    prior_season = target_season - 1
    panel = season_panel([prior_season])
    panel = (
        panel
        .join(snap_features([prior_season]), on=["player_id", "season"], how="left")
        .join(expected_points_features([prior_season]),
              on=["player_id", "season"], how="left")
        .join(team_context([prior_season]), on=["team", "season"], how="left")
        .with_columns(
            (pl.col("total_points") - pl.col("expected_points"))
            .alias("points_over_expected")
        )
    )

    feats = [c for c in FEATURE_COLUMNS if c in panel.columns]
    frame = (
        panel.select(["player_id", "season", "position", "player_name", "team"] + feats)
        .with_columns((pl.col("season") + 1).alias("season"))
        .rename({c: f"prior_{c}" for c in feats})
        .join(bio_features(), on="player_id", how="left")
    )

    if "age" in frame.columns:
        frame = frame.with_columns(
            (pl.col("age") - (config.RAW_SEASON_END + 1 - pl.col("season"))).alias("age")
        )

    return frame.sort("player_id")


def training_frame(
    table: pl.DataFrame,
    label: str = "total_points",
    min_prior_games: int = 4,
) -> pl.DataFrame:
    """Filter to rows usable for supervised training on `label`."""
    return table.filter(
        pl.col("prior_games").ge(min_prior_games) & pl.col(label).is_not_null()
    )


def save(table: pl.DataFrame) -> None:
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    table.write_parquet(config.PROCESSED / "features.parquet")


def load() -> pl.DataFrame:
    path = config.PROCESSED / "features.parquet"
    if not path.exists():
        raise FileNotFoundError("features not built; run scripts/build_features.py")
    return pl.read_parquet(path)
