"""Depth chart and injury status — who is actually playing this week.

WHY THIS EXISTS

A backup's entire value is one number: where he sits on his own depth chart.
Nothing else about him matters as much, and it is the fastest-moving fact in
the sport. Jeremiyah Love and Tyler Allgeier swapped places on Arizona's chart
during camp, which changes both their values completely, and nothing in a
board built from July ADP would ever notice.

Pre-season rank encodes the depth chart as it stood in July, and then quietly
goes stale for the rest of the year.

TWO DIFFERENT FACTS, KEPT APART

    depth chart   who has the job. Changes the RATE -- a back who inherits a
                  starting job is a different player, not the same player with
                  more games.
    injury        whether he plays. Changes AVAILABILITY. It must never touch
                  the rate, because a player who left in the first quarter did
                  not have a bad game.

Collapsing those two is the mistake that makes injured stars look washed and
makes healthy backups look startable.

BOTH ARE FREE

nflverse publishes both, keyed on gsis_id, which is already the board's
player_id -- so no crosswalk, and none of the id-lag hazards that have bitten
every other join in this project.
"""

from __future__ import annotations

import polars as pl

from fantasyedge import config

# ESPN's depth charts carry every position group; only these matter to fantasy.
FANTASY_POS = frozenset({"QB", "RB", "WR", "TE", "FB"})

# What a starting job is worth relative to the man behind him, by position.
# A backup running back is nearly worthless until he is not; a WR3 still runs
# routes every week. These scale the projection when the chart disagrees with
# where the board has him.
BACKUP_DISCOUNT: dict[str, tuple[float, ...]] = {
    #        rank1  rank2  rank3  rank4+
    "RB":   (1.00,  0.55,  0.30,  0.15),
    "WR":   (1.00,  0.85,  0.65,  0.35),
    "TE":   (1.00,  0.45,  0.20,  0.10),
    "QB":   (1.00,  0.15,  0.05,  0.02),
}

# Report status -> share of remaining games you should expect him to play.
# "Questionable" is the interesting one: it is close to a coin flip in name
# only -- questionable players play the large majority of the time.
STATUS_AVAILABILITY: dict[str, float] = {
    "Out": 0.0,
    "Doubtful": 0.08,
    "Questionable": 0.75,
    "Injured Reserve": 0.0,
    "IR": 0.0,
    "PUP": 0.0,
    "Suspension": 0.0,
}


def depth_chart(season: int) -> pl.DataFrame:
    """Latest published depth chart, one row per player.

    The feed is a full snapshot every time it is republished, so it carries
    hundreds of thousands of rows across a season. Only the most recent
    snapshot is the depth chart; everything before it is history.
    """
    import nflreadpy as nfl

    d = nfl.load_depth_charts(seasons=[season])
    if not d.height:
        return pl.DataFrame()

    latest = d.filter(pl.col("dt") == d["dt"].max())
    return (
        latest.filter(pl.col("pos_abb").is_in(list(FANTASY_POS)))
        .select([
            pl.col("gsis_id").alias("player_id"),
            pl.col("player_name").alias("chart_name"),
            pl.col("team").alias("chart_team"),
            pl.col("pos_abb").alias("chart_pos"),
            pl.col("pos_rank").cast(pl.Int32).alias("depth_rank"),
        ])
        .filter(pl.col("player_id").is_not_null())
        .unique(subset=["player_id"], keep="first")
    )


def injuries(season: int, week: int | None = None) -> pl.DataFrame:
    """Most recent injury report per player."""
    import nflreadpy as nfl

    d = nfl.load_injuries(seasons=[season])
    if not d.height:
        return pl.DataFrame()
    if week is not None:
        d = d.filter(pl.col("week") <= week)
    if not d.height:
        return pl.DataFrame()

    return (
        d.sort("week")
        .group_by("gsis_id")
        .last()
        .select([
            pl.col("gsis_id").alias("player_id"),
            pl.col("report_status").alias("injury_status"),
            pl.col("report_primary_injury").alias("injury"),
            pl.col("week").alias("injury_week"),
        ])
        .filter(pl.col("player_id").is_not_null())
    )


def _scale(pos_col: str, rank_col: str) -> pl.Expr:
    """The BACKUP_DISCOUNT value for a (position, rank) pair."""
    expr = pl.lit(1.0)
    for pos, scale in BACKUP_DISCOUNT.items():
        rank_expr = pl.lit(scale[-1])
        for i, mult in enumerate(scale):
            rank_expr = (pl.when(pl.col(rank_col) == i + 1)
                         .then(pl.lit(mult)).otherwise(rank_expr))
        expr = (pl.when(pl.col(pos_col) == pos).then(rank_expr).otherwise(expr))
    return pl.when(pl.col(rank_col).is_null()).then(pl.lit(1.0)).otherwise(expr)


def _with_expectation(board: pl.DataFrame, chart: pl.DataFrame) -> pl.DataFrame:
    """Join the chart and work out what the MARKET already assumed about role.

    THE DOUBLE-COUNT THIS AVOIDS

    Discounting everyone whose depth rank is worse than 1 flags Tee Higgins,
    Davante Adams and George Pickens as demoted, and deflates half the
    receivers in the league. They are WR2s. Everybody knows they are WR2s --
    that is exactly why they go where they go in drafts, and their ADP has
    priced it since July.

    A depth chart is only news when it DISAGREES with the market. So the board
    is asked what it already believed: rank each player among his own team\'s
    players at his position, by projection. If ADP has a man as his team\'s RB1
    and the chart has him RB2, that is a demotion worth pricing. If ADP already
    had him second, nothing has happened.
    """
    b = board.join(chart.drop(["chart_name", "chart_pos"]), on="player_id", how="left")
    return b.with_columns(
        pl.col("projected_points")
        .rank("ordinal", descending=True)
        .over(["chart_team", "position"])
        .cast(pl.Int32)
        .alias("expected_rank")
    ).with_columns(
        # Ratio of what the chart says to what the market assumed. Clipped at 1
        # so the chart only ever moves a player DOWN -- being listed first in a
        # committee is not evidence, and promoting off it would hand every
        # backup his starter\'s projection.
        pl.min_horizontal(
            _scale("position", "depth_rank") / _scale("position", "expected_rank"),
            pl.lit(1.0),
        ).fill_null(1.0).alias("depth_mult")
    )


def apply(board: pl.DataFrame, season: int, week: int | None = None) -> pl.DataFrame:
    """Fold depth chart and injury status onto a board."""
    chart = depth_chart(season)
    inj = injuries(season, week)

    out = board
    if chart.height:
        out = _with_expectation(board, chart)
        out = out.with_columns(
            (pl.col("projected_points") * pl.col("depth_mult")).alias("projected_points")
        )
        for q in ("season_p20", "season_p50", "season_p80"):
            if q in out.columns:
                out = out.with_columns((pl.col(q) * pl.col("depth_mult")).alias(q))

    if inj.height:
        out = out.join(inj, on="player_id", how="left")
        avail = pl.col("injury_status").replace_strict(
            STATUS_AVAILABILITY, default=1.0, return_dtype=pl.Float64)
        # Availability, NOT rate. This is the whole point of keeping the two
        # facts apart -- an injury shortens the season, it does not make him
        # worse per game when he does play.
        out = out.with_columns(
            (pl.col("expected_games") * avail.fill_null(1.0)).alias("expected_games")
        )

    return out


def changed(board: pl.DataFrame, season: int, top: int = 40) -> pl.DataFrame:
    """Players their own team has moved BELOW where the market has them.

    The alert list, and the most actionable thing in this module: sell
    candidates the room has not repriced yet.
    """
    chart = depth_chart(season)
    if not chart.height:
        return pl.DataFrame()

    b = _with_expectation(board, chart).filter(
        pl.col("depth_rank").is_not_null() & (pl.col("depth_mult") < 1.0)
    )
    if not b.height:
        return pl.DataFrame()

    return (
        b.sort("projected_points", descending=True)
        .head(top)
        .select(["player_id", "player_name", "position", "chart_team",
                 "expected_rank", "depth_rank", "depth_mult", "projected_points"])
    )
