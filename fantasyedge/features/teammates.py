"""Teammate context — who else is on the field, and who is not.

THE EFFECT

Fantasy value is a claim on a finite pool of opportunity. A team throws ~35
passes a week; every target one receiver takes is a target another does not.
So a player's outlook depends less on his own talent than on who he is
competing with for touches, and that changes week to week.

When the WR1 is ruled out on Friday, his ~25% target share does not evaporate
-- it redistributes to whoever is left. The WR2 who normally sees 6 targets
sees 9. That is the single most exploitable weekly signal in fantasy, and
nothing in the model saw it until now.

WHAT THIS BUILDS

  vacated_target_share   share belonging to teammates ruled out this week
  vacated_carry_share    same for the ground game
  team_pos_rank          his standing among healthy teammates at his position
  teammates_out          how many same-position competitors are unavailable
  target_concentration   how funnelled the offence is (HHI)

LEAKAGE

Injury reports publish Wednesday through Friday, so week N status is known
before week N kicks off and is legitimately a feature. The *shares* used to
weight it are rolling averages through week N-1, never including the current
week.
"""

from __future__ import annotations

import polars as pl

from fantasyedge import config
from fantasyedge.data import pull

# Report statuses that mean the player will very likely not play.
OUT_STATUSES = ("Out", "Doubtful")


def weekly_availability(seasons: list[int]) -> pl.DataFrame:
    """Per player-week: is he ruled out?

    `injuries` types season and week as Float, unlike the rest of nflverse.
    """
    inj = pull.load("injuries")
    return (
        inj.with_columns([
            pl.col("season").cast(pl.Int32, strict=False),
            pl.col("week").cast(pl.Int32, strict=False),
        ])
        .filter(
            pl.col("season").is_in(seasons)
            & pl.col("gsis_id").is_not_null()
            # season_type is NULL on ~93% of rows here, so filtering it to
            # "REG" silently discards almost the entire table. Bound the week
            # instead; regular season is weeks 1-18.
            & (pl.col("week") <= 18)
        )
        .select([
            pl.col("gsis_id").alias("player_id"),
            "season", "week",
            pl.col("report_status").is_in(list(OUT_STATUSES)).alias("is_out"),
            (pl.col("report_status") == "Questionable").alias("is_questionable"),
        ])
        .unique(subset=["player_id", "season", "week"], keep="first")
    )


def build(weekly: pl.DataFrame) -> pl.DataFrame:
    """Attach teammate-context features to a week-grain table.

    `weekly` must carry player_id, season, week, team, position, and the
    rolling shares tgt_share_l6 / carries_l6 built by features.weekly.
    """
    seasons = sorted(weekly["season"].unique().to_list())
    avail = weekly_availability(seasons)

    # player_stats only contains players who actually PLAYED, so a player
    # ruled out has no row that week -- which is precisely the player whose
    # share we need to count as vacated. Build a grid over every week of each
    # player's season and carry his last known team and share forward, so the
    # weeks he misses still exist and still carry his usual workload.
    grid = (
        weekly.select(["player_id", "season"]).unique()
        .join(
            pl.DataFrame({"week": list(range(1, 19))},
                         schema={"week": weekly["week"].dtype}),
            how="cross",
        )
        .join(
            weekly.select(["player_id", "season", "week", "team", "position",
                           "tgt_share_l6", "carries_l6"]),
            on=["player_id", "season", "week"], how="left",
        )
        .sort(["player_id", "season", "week"])
        .with_columns([
            pl.col("team").forward_fill().over(["player_id", "season"]),
            pl.col("position").forward_fill().over(["player_id", "season"]),
            pl.col("tgt_share_l6").forward_fill().over(["player_id", "season"]),
            pl.col("carries_l6").forward_fill().over(["player_id", "season"]),
        ])
        .join(avail, on=["player_id", "season", "week"], how="left")
        .with_columns([
            pl.col("is_out").fill_null(False),
            pl.col("tgt_share_l6").fill_null(0.0).alias("_tgt"),
            pl.col("carries_l6").fill_null(0.0).alias("_car"),
        ])
        .filter(pl.col("team").is_not_null())
    )

    df = weekly.join(avail, on=["player_id", "season", "week"], how="left").with_columns([
        pl.col("is_out").fill_null(False),
        pl.col("is_questionable").fill_null(False),
        pl.col("tgt_share_l6").fill_null(0.0).alias("_tgt"),
        pl.col("carries_l6").fill_null(0.0).alias("_car"),
    ])

    team_key = ["team", "season", "week"]

    # Vacated pools come from the GRID, not from df -- the players who matter
    # here are the ones missing from df.
    out_pool = (
        grid.filter(pl.col("is_out"))
        .group_by(team_key)
        .agg([
            pl.col("_tgt").sum().alias("_team_vacated_tgt"),
            pl.col("_car").sum().alias("_team_vacated_car"),
            pl.len().alias("_team_out_count"),
        ])
    )

    # Team totals for concentration and ranking.
    team_totals = (
        df.group_by(team_key)
        .agg([
            pl.col("_tgt").sum().alias("_team_tgt"),
            (pl.col("_tgt") ** 2).sum().alias("_team_tgt_hhi"),
        ])
    )

    df = (
        df.join(out_pool, on=team_key, how="left")
        .join(team_totals, on=team_key, how="left")
        .with_columns([
            pl.col("_team_vacated_tgt").fill_null(0.0),
            pl.col("_team_vacated_car").fill_null(0.0),
            pl.col("_team_out_count").fill_null(0),
        ])
    )

    # A player does not inherit his own vacated share. Subtract himself.
    df = df.with_columns([
        (pl.col("_team_vacated_tgt")
         - pl.when(pl.col("is_out")).then(pl.col("_tgt")).otherwise(0.0)
         ).clip(0.0, None).alias("vacated_target_share"),

        (pl.col("_team_vacated_car")
         - pl.when(pl.col("is_out")).then(pl.col("_car")).otherwise(0.0)
         ).clip(0.0, None).alias("vacated_carry_share"),

        (pl.col("_team_out_count")
         - pl.when(pl.col("is_out")).then(1).otherwise(0)
         ).alias("teammates_out"),
    ])

    # Competitors at his own position who are unavailable -- the sharpest
    # version of the effect, since a WR mostly inherits from other WRs.
    pos_key = ["team", "season", "week", "position"]
    pos_out = (
        grid.filter(pl.col("is_out"))
        .group_by(pos_key)
        .agg([
            pl.col("_tgt").sum().alias("_pos_vacated_tgt"),
            pl.len().alias("_pos_out_count"),
        ])
    )

    df = df.join(pos_out, on=pos_key, how="left").with_columns([
        pl.col("_pos_vacated_tgt").fill_null(0.0),
        pl.col("_pos_out_count").fill_null(0),
    ]).with_columns([
        (pl.col("_pos_vacated_tgt")
         - pl.when(pl.col("is_out")).then(pl.col("_tgt")).otherwise(0.0)
         ).clip(0.0, None).alias("vacated_target_share_same_pos"),

        (pl.col("_pos_out_count")
         - pl.when(pl.col("is_out")).then(1).otherwise(0)
         ).alias("teammates_out_same_pos"),
    ])

    # Standing among *healthy* teammates at his position: WR1, WR2, WR3.
    # Computed on availability-adjusted shares so a promotion shows up.
    df = df.with_columns(
        pl.when(pl.col("is_out")).then(0.0).otherwise(pl.col("_tgt"))
        .alias("_healthy_tgt")
    ).with_columns(
        pl.col("_healthy_tgt").rank("ordinal", descending=True)
        .over(pos_key).cast(pl.Int32).alias("team_pos_rank")
    )

    df = df.with_columns([
        # How funnelled the offence is. A concentrated offence has more to
        # redistribute when its focal player sits.
        pl.when(pl.col("_team_tgt") > 0)
        .then(pl.col("_team_tgt_hhi") / (pl.col("_team_tgt") ** 2))
        .otherwise(None)
        .alias("target_concentration"),

        (pl.col("team_pos_rank") == 1).alias("is_top_option_at_position"),

        # Opportunity he could plausibly absorb: what is free, scaled by how
        # high he sits in the pecking order.
        pl.when(pl.col("team_pos_rank").is_not_null())
        .then(pl.col("vacated_target_share_same_pos")
              / pl.col("team_pos_rank").cast(pl.Float64))
        .otherwise(None)
        .alias("absorbable_target_share"),
    ])

    drop = [c for c in df.columns if c.startswith("_")]
    return df.drop(drop)


NEW_FEATURES = [
    "vacated_target_share", "vacated_carry_share",
    "vacated_target_share_same_pos", "teammates_out",
    "teammates_out_same_pos", "team_pos_rank",
    "target_concentration", "is_top_option_at_position",
    "absorbable_target_share", "is_out", "is_questionable",
]
