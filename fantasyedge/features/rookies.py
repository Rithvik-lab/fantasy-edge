"""Rookie projection — draft capital, athleticism, and landing spot.

WHY ROOKIES NEED THEIR OWN MODEL

Every other model here is built on a player's prior NFL season. Rookies have
none, so they are excluded from the main feature table by construction. That
is a real hole: a large share of any draft board is first-year players, and
they carry the widest outcomes on it. Omarion Hampton and Malik Nabers were
both first-round skill picks; one returned nothing and one finished top-of-
position. Nothing in the model could tell them apart because neither existed
in it.

WHAT REPLACES PRIOR-SEASON PRODUCTION

  draft capital   overall pick and round. The market's own evaluation, and by
                  far the strongest single input -- teams pay for talent and
                  then play what they paid for.
  position rank   Nth back or receiver taken. Being the first at your position
                  is different from being the eighth, even at a similar pick.
  athleticism     combine testing, plus derived speed score, which weights
                  forty time by mass and matters more at RB than anywhere.
  landing spot    how much opportunity actually exists on the drafting team.
                  A receiver drafted behind an established WR1 is a different
                  asset from the same player drafted into a vacuum.

The landing-spot half is the part public rookie rankings handle worst, and it
is computed here from the drafting team's PRIOR season, so it is knowable on
draft night.
"""

from __future__ import annotations

import polars as pl

from fantasyedge import config
from fantasyedge.data import pull

# Programs that reliably produce NFL skill players. A crude proxy for
# strength of competition faced.
POWER_CONFERENCE = {
    "Alabama", "Georgia", "Ohio St.", "LSU", "Clemson", "Oklahoma", "Texas",
    "Michigan", "Notre Dame", "Florida", "Penn St.", "USC", "Auburn",
    "Texas A&M", "Oregon", "Wisconsin", "Miami (FL)", "Florida St.",
    "Tennessee", "Iowa", "Washington", "Mississippi", "Missouri",
    "Oklahoma St.", "South Carolina", "Michigan St.", "Nebraska", "UCLA",
    "Arkansas", "Kentucky", "Baylor", "TCU", "Louisville", "Pittsburgh",
    "North Carolina", "NC State", "Virginia Tech", "Stanford", "Utah",
    "Arizona St.", "Mississippi St.", "Purdue", "Minnesota", "Indiana",
    "Illinois", "Maryland", "Rutgers", "Boston College", "Duke", "Syracuse",
    "Wake Forest", "Virginia", "Kansas St.", "Iowa St.", "West Virginia",
    "Cincinnati", "Houston", "UCF", "BYU", "Colorado", "Arizona",
    "California", "Oregon St.", "Washington St.", "Texas Tech",
}

TEAM_FIXES = {
    "LVR": "LV", "OAK": "LV", "SDG": "LAC", "SD": "LAC", "STL": "LA",
    "LAR": "LA", "RAM": "LA", "GNB": "GB", "KAN": "KC", "NWE": "NE",
    "NOR": "NO", "SFO": "SF", "TAM": "TB", "JAC": "JAX", "WAS": "WAS",
    "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU",
}


def _fix_team(col: str) -> pl.Expr:
    expr = pl.col(col)
    for bad, good in TEAM_FIXES.items():
        expr = pl.when(pl.col(col) == bad).then(pl.lit(good)).otherwise(expr)
    return expr


def draft_class(seasons: list[int]) -> pl.DataFrame:
    """Drafted skill players with capital and position-rank features."""
    d = pl.read_parquet(config.RAW / "draft_picks.parquet")

    df = (
        d.filter(
            pl.col("season").is_in(seasons)
            & pl.col("position").is_in(list(config.MODELED_POSITIONS))
        )
        .select([
            "season", "round", "pick", "gsis_id", "pfr_player_id",
            pl.col("pfr_player_name").alias("player_name"),
            "position", "college", _fix_team("team").alias("team"),
        ])
    )

    return df.with_columns([
        # Nth player at his position taken that year. Being RB1 of a class is
        # a different signal from being RB8 at a similar overall pick.
        pl.col("pick").rank("ordinal").over(["season", "position"])
        .cast(pl.Int32).alias("pos_draft_rank"),
        pl.col("college").is_in(POWER_CONFERENCE).alias("power_conference"),
        # Draft capital decays steeply; log makes pick 5 vs 15 count for more
        # than pick 205 vs 215.
        (pl.col("pick").cast(pl.Float64) + 1).log().alias("log_pick"),
    ])


def combine_features() -> pl.DataFrame:
    """Athletic testing, plus speed score."""
    c = pl.read_parquet(config.RAW / "combine.parquet")
    keep = [x for x in ("pfr_id", "ht", "wt", "forty", "bench", "vertical",
                        "broad_jump", "cone", "shuttle") if x in c.columns]
    out = c.select(keep).filter(pl.col("pfr_id").is_not_null())

    # Height ships as "6-2", not inches.
    if "ht" in out.columns and out["ht"].dtype == pl.String:
        out = out.with_columns(
            (pl.col("ht").str.split("-").list.get(0).cast(pl.Float64, strict=False) * 12
             + pl.col("ht").str.split("-").list.get(1).cast(pl.Float64, strict=False))
            .alias("ht")
        )

    if {"wt", "forty"}.issubset(set(out.columns)):
        out = out.with_columns(
            # Speed score: forty time weighted by mass. A 4.5 at 220 lbs is a
            # very different athlete from a 4.5 at 185, and this is the
            # standard way of saying so.
            pl.when(pl.col("forty") > 0)
            .then(pl.col("wt") * 200.0 / (pl.col("forty") ** 4))
            .otherwise(None)
            .alias("speed_score")
        )
    if {"ht", "wt"}.issubset(set(out.columns)):
        out = out.with_columns(
            pl.when(pl.col("ht") > 0)
            .then(pl.col("wt") * 703.0 / (pl.col("ht") ** 2))
            .otherwise(None)
            .alias("bmi")
        )
    return out.unique(subset=["pfr_id"], keep="first")


def landing_spot(seasons: list[int]) -> pl.DataFrame:
    """Opportunity available on the drafting team, from its PRIOR season.

    The question a rookie's value hangs on is not just "how good is he" but
    "who is in front of him". A team whose lead receiver commanded a quarter
    of the targets has far less to give than one whose top option took 15%.

    Everything here is measured the season BEFORE the rookie arrives, so it is
    known on draft night.
    """
    src = sorted({s - 1 for s in seasons})
    w = (
        pull.load("player_stats")
        .filter(
            pl.col("season").is_in(src)
            & (pl.col("season_type") == "REG")
            & pl.col("position").is_in(list(config.MODELED_POSITIONS))
        )
    )

    by_player = (
        w.group_by(["team", "season", "position", "player_id"])
        .agg([
            pl.col("targets").sum().alias("tg"),
            pl.col("carries").sum().alias("ca"),
            pl.col("fantasy_points_ppr").sum().alias("pts"),
        ])
    )

    team_pos = (
        by_player.group_by(["team", "season", "position"])
        .agg([
            pl.col("tg").sum().alias("pos_targets"),
            pl.col("ca").sum().alias("pos_carries"),
            pl.col("pts").max().alias("top_player_points"),
            pl.col("pts").sum().alias("pos_points"),
            pl.len().alias("pos_bodies"),
        ])
    )

    team_tot = (
        by_player.group_by(["team", "season"])
        .agg([
            pl.col("tg").sum().alias("team_targets"),
            pl.col("ca").sum().alias("team_carries"),
        ])
    )

    out = team_pos.join(team_tot, on=["team", "season"], how="left").with_columns([
        # How dominant the incumbent is. High = the rookie is buried.
        pl.when(pl.col("pos_points") > 0)
        .then(pl.col("top_player_points") / pl.col("pos_points"))
        .otherwise(None)
        .alias("incumbent_dominance"),
        pl.when(pl.col("team_targets") > 0)
        .then(pl.col("pos_targets") / pl.col("team_targets"))
        .otherwise(None)
        .alias("pos_target_share"),
    ])

    # Shift forward: the prior season's shape applies to the rookie's year.
    return out.with_columns((pl.col("season") + 1).alias("season")).rename({
        "pos_points": "prior_pos_points",
        "top_player_points": "prior_top_player_points",
        "pos_bodies": "prior_pos_bodies",
        "pos_targets": "prior_pos_targets",
        "pos_carries": "prior_pos_carries",
        "team_targets": "prior_team_targets",
        "team_carries": "prior_team_carries",
    })


def rookie_outcomes(seasons: list[int]) -> pl.DataFrame:
    """What each rookie actually produced in year one."""
    w = (
        pull.load("player_stats")
        .filter(
            pl.col("season").is_in(seasons)
            & (pl.col("season_type") == "REG")
            & pl.col("position").is_in(list(config.MODELED_POSITIONS))
        )
    )
    return (
        w.group_by(["player_id", "season"])
        .agg([
            pl.col("fantasy_points_ppr").sum().alias("rookie_points"),
            pl.col("fantasy_points_ppr").mean().alias("rookie_ppg"),
            pl.len().alias("rookie_games"),
        ])
        .rename({"player_id": "gsis_id"})
    )


def build(seasons: list[int] | None = None) -> pl.DataFrame:
    """One row per drafted skill player, with features and rookie outcome."""
    if seasons is None:
        seasons = list(range(2006, config.RAW_SEASON_END + 2))

    cls = draft_class(seasons)
    comb = combine_features()
    land = landing_spot(seasons)
    out = rookie_outcomes(seasons)

    df = (
        cls.join(comb, left_on="pfr_player_id", right_on="pfr_id", how="left")
        .join(land, on=["team", "season", "position"], how="left")
        .join(out, on=["gsis_id", "season"], how="left")
    )

    # Positional finish among rookies that year -- the fair way to compare a
    # 2011 season against a 2024 one, since scoring environments differ.
    return df.with_columns([
        pl.col("rookie_points").fill_null(0.0),
        pl.col("rookie_games").fill_null(0),
    ]).with_columns(
        pl.col("rookie_points").rank("ordinal", descending=True)
        .over(["season", "position"]).cast(pl.Int32).alias("rookie_pos_finish")
    )


FEATURES = [
    "round", "pick", "log_pick", "pos_draft_rank", "power_conference",
    "ht", "wt", "forty", "bench", "vertical", "broad_jump", "cone", "shuttle",
    "speed_score", "bmi",
    "incumbent_dominance", "pos_target_share", "prior_pos_points",
    "prior_top_player_points", "prior_pos_bodies", "prior_pos_targets",
    "prior_pos_carries",
]


def trainable(df: pl.DataFrame, seasons: list[int]) -> pl.DataFrame:
    """Rows with a completed rookie season."""
    return df.filter(pl.col("season").is_in(seasons))
