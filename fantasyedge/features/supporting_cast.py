"""Supporting cast — the teammates who determine what a player can do.

THE GAP THIS FILLS

Every feature in the main model describes a player's own history. None of them
know who else is in the huddle. That is a real omission:

  a quarterback throwing to Chase and Higgins is not the same asset as one
  throwing to a single alpha and nothing else, even with identical history

  a receiver's value depends on his quarterback and on how many other mouths
  are competing for the same targets

  a player returning from a lost season looks terrible in prior-season
  features and may be the same player he was two years ago

RETURNING PLAYERS

The last one is why prior-season production misleads. Somebody who played four
games in 2025 carries a tiny point total into every lagged feature, and the
model reads that as decline rather than absence. The market does not make that
mistake -- it prices him on expectation. So the *gap* between what the market
pays and what he produced is a usable signal for exactly this case, and it
costs nothing to compute.

CURRENT ROSTERS

Supporting cast has to come from *current* rosters, not last season's, or it
misses every trade and signing. ESPN's board carries live team assignments,
which is why it is the source here.
"""

from __future__ import annotations

import polars as pl

from fantasyedge import config
from fantasyedge.data import pull

# Rough conversion from draft position to value. ADP is ordinal; this makes it
# roughly linear in expected points so teammates can be summed.
def _adp_value(col: str = "adp") -> pl.Expr:
    return (250.0 / (pl.col(col) + 20.0)).clip(0.0, 12.0)


def cast_from_board(board: pl.DataFrame, team_col: str = "team") -> pl.DataFrame:
    """Per-team supporting-cast strength from a live board.

    `board` needs player_name, position, an ADP column, and a team column.
    """
    if team_col not in board.columns:
        raise KeyError(f"board has no {team_col!r} column")

    b = board.with_columns(_adp_value().alias("_val"))

    catchers = b.filter(pl.col("position").is_in(["WR", "TE"]))
    backs = b.filter(pl.col("position") == "RB")
    qbs = b.filter(pl.col("position") == "QB")

    cast = (
        catchers.group_by(team_col)
        .agg([
            pl.col("_val").sum().alias("pass_catcher_value"),
            pl.col("_val").max().alias("top_catcher_value"),
            pl.col("_val").sort(descending=True).head(3).sum()
              .alias("top3_catcher_value"),
            pl.len().alias("n_catchers"),
        ])
    )

    qb_val = (
        qbs.group_by(team_col)
        .agg(pl.col("_val").max().alias("qb_value"))
    )

    rb_val = (
        backs.group_by(team_col)
        .agg(pl.col("_val").max().alias("top_back_value"))
    )

    out = cast.join(qb_val, on=team_col, how="left").join(
        rb_val, on=team_col, how="left")

    return out.with_columns(
        # How concentrated the receiving room is. A single alpha and nothing
        # else is a different situation from three good options, and it
        # matters in opposite directions for the alpha and for the QB.
        pl.when(pl.col("pass_catcher_value") > 0)
        .then(pl.col("top_catcher_value") / pl.col("pass_catcher_value"))
        .otherwise(None)
        .alias("catcher_concentration")
    )


def attach(board: pl.DataFrame, team_col: str = "team") -> pl.DataFrame:
    """Give every player his own team context, from his own perspective."""
    cast = cast_from_board(board, team_col)
    out = board.join(cast, on=team_col, how="left")

    return out.with_columns([
        # A quarterback's weapons; a pass-catcher's competition. Same column,
        # opposite meaning by position, which the model can learn.
        pl.when(pl.col("position") == "QB")
        .then(pl.col("top3_catcher_value"))
        .otherwise(pl.col("top3_catcher_value") - _adp_value())
        .alias("teammate_support"),

        # For a receiver, who is throwing to him.
        pl.when(pl.col("position").is_in(["WR", "TE"]))
        .then(pl.col("qb_value"))
        .otherwise(None)
        .alias("supporting_qb_value"),
    ])


def returning_players(
    board: pl.DataFrame, season: int | None = None, min_gap: float = 0.35
) -> pl.DataFrame:
    """Players the market values far above what they produced last season.

    Mostly three groups: rookies, breakout bets, and players who missed time.
    The third is the one the main model handles worst -- a lost season drags
    every lagged feature down and reads as decline rather than absence.

    `market_vs_production` is the gap, in percentile terms. Large and positive
    means the market expects much more than last year suggests.
    """
    season = season or config.RAW_SEASON_END

    stats = (
        pull.load("player_stats")
        .filter(
            (pl.col("season") == season)
            & (pl.col("season_type") == "REG")
            & pl.col("position").is_in(list(config.MODELED_POSITIONS))
        )
        .group_by(["player_id", "position"])
        .agg([
            pl.col("fantasy_points_ppr").sum().alias("prior_points"),
            pl.len().alias("prior_games"),
        ])
    )

    joined = board.join(stats, on="player_id", how="left").with_columns([
        pl.col("prior_points").fill_null(0.0),
        pl.col("prior_games").fill_null(0),
    ])

    ranked = joined.with_columns([
        (1.0 - pl.col("ecr").rank("average") / pl.len()).alias("_market_pct"),
        (pl.col("prior_points").rank("average").over("position")
         / pl.len().over("position")).alias("_prod_pct"),
    ])

    return ranked.with_columns(
        (pl.col("_market_pct") - pl.col("_prod_pct")).alias("market_vs_production")
    ).with_columns(
        # Missed time last year and the market still likes him -- the profile
        # where lagged features understate a player most.
        ((pl.col("market_vs_production") >= min_gap)
         & (pl.col("prior_games") <= 12)
         & (pl.col("prior_games") > 0)).alias("likely_returning")
    ).drop(["_market_pct", "_prod_pct"])


NEW_FEATURES = [
    "pass_catcher_value", "top_catcher_value", "top3_catcher_value",
    "qb_value", "top_back_value", "catcher_concentration",
    "teammate_support", "supporting_qb_value",
    "market_vs_production", "likely_returning",
]
