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
    """Most recent injury report per player.

    A season nobody has played yet has no injury report, and nflreadpy says so
    by RAISING rather than returning nothing -- "Season must be between 2009
    and 2025" for a 2026 request in August. An empty frame is the right answer
    to "who is hurt in a season that has not started", so it is returned here
    instead of taking the caller down with it.
    """
    import nflreadpy as nfl

    try:
        d = nfl.load_injuries(seasons=[season])
    except Exception:
        return pl.DataFrame()
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


def roles(board: pl.DataFrame, player_ids: list[str], season: int) -> list[dict]:
    """Who these men share a job with, and whether the price already knows.

    THE QUESTION BEHIND A TRADE FOR A RUNNING BACK: he is one of two backs in
    that building -- is he still worth it? The answer is nearly always "the
    market already knew that", and saying so is more useful than a warning.
    George Pickens is a WR2, everybody knows he is a WR2, and it is exactly why
    he goes where he goes in drafts. His ADP has priced the committee since
    July.

    So each man comes back with three facts and no adjustment: where the chart
    lists him, who is ahead of him, and whether that matches what the board
    already assumed. Only the third can be news. `expected_rank` is his rank
    among his own team's players at his position BY PROJECTION -- what the
    market believed -- so chart 2 against expected 2 is a committee that is
    already in the price, and chart 2 against expected 1 is a demotion nobody
    has paid for yet.
    """
    if not player_ids:
        return []
    chart = depth_chart(season)
    if not chart.height:
        return []
    j = _with_expectation(board, chart)
    hurt = injuries(season)
    if hurt.height:
        j = j.join(hurt.select(["player_id", "injury_status"]),
                   on="player_id", how="left")

    ahead: dict[tuple, list[str]] = {}
    for r in chart.iter_rows(named=True):
        ahead.setdefault((r["chart_team"], r["chart_pos"]), []).append(
            (r["depth_rank"], r["chart_name"]))

    out = []
    for r in j.filter(pl.col("player_id").is_in(player_ids)).iter_rows(named=True):
        rank = r.get("depth_rank")
        team = r.get("chart_team")
        if rank is None or team is None:
            continue
        room = sorted(ahead.get((team, r.get("position")), []))
        out.append({
            "player_id": r["player_id"],
            "player_name": r["player_name"],
            "position": r["position"],
            "team": team,
            "depth_rank": int(rank),
            "expected_rank": int(r["expected_rank"]) if r.get("expected_rank") else None,
            "ahead": [n for k, n in room if k < rank][:2],
            "behind": [n for k, n in room if k > rank][:1],
            # 1.0 means the committee is already in his price.
            "discount": round(float(r.get("depth_mult") or 1.0), 3),
            "injury_status": r.get("injury_status"),
        })
    return sorted(out, key=lambda x: x["discount"])


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


# ---------------------------------------------------------------------------
# Vacancy: what the man ahead of you being out is worth
# ---------------------------------------------------------------------------
# MEASURED on 2025, not assumed. For every team-position-week, whether the
# number one appeared at all, against what the men behind him scored:
#
#            starter in   starter OUT    lift    n
#   RB2            6.93         13.00   1.88x   28
#   RB3            2.80          5.48   1.95x   24
#   WR2            9.34         10.34   1.11x   36
#   WR3            6.43          8.26   1.28x   31
#   TE2            4.17          5.84   1.40x   62
#   TE3            2.16          2.86   1.33x   51
#
# The ordering is the whole point and it is much sharper than "backups gain".
# A running back behind an injured starter nearly DOUBLES; a receiver behind an
# injured starter barely moves. Carries are a fixed pie that transfers whole to
# the next back. Vacated targets scatter across the entire receiving corps --
# other receivers, the tight end, the backs -- so no single WR2 inherits much.
#
# Small samples (n = 24-62 player-weeks), so these are rounded toward 1.0
# rather than used raw. The direction and the RB/WR gap are not in doubt; the
# second decimal place is.
VACANCY_LIFT: dict[str, tuple[float, float]] = {
    #        depth2  depth3+
    "RB":   (1.80,   1.85),
    "WR":   (1.10,   1.22),
    "TE":   (1.35,   1.28),
    "QB":   (1.60,   1.20),   # not measured: too few QB2 weeks to fit
}

# Statuses that mean he is not playing. "Questionable" is excluded on purpose:
# questionable players play the large majority of the time, so treating it as
# a vacancy would inflate every backup in the league every single week.
GONE = frozenset({"Out", "Doubtful", "Injured Reserve", "IR", "PUP", "Suspension"})


def vacancy(board: pl.DataFrame, season: int, week: int | None = None) -> pl.DataFrame:
    """Promote the next man up when the starter ahead of him is out.

    This is the in-season half of the depth chart. `apply` handles the static
    case -- who has the job. This handles the case that actually moves trade
    value week to week: the job just came open.

    Adds `vacancy_mult` and applies it, so a backup's projection reflects the
    role he is about to have rather than the one he had in August.
    """
    chart = depth_chart(season)
    inj = injuries(season, week)
    if not chart.height or not inj.height:
        return board.with_columns(pl.lit(1.0).alias("vacancy_mult"))

    hurt = (inj.filter(pl.col("injury_status").is_in(list(GONE)))
               .select("player_id").unique())
    if not hurt.height:
        return board.with_columns(pl.lit(1.0).alias("vacancy_mult"))

    # Which team-positions have their number one sidelined?
    sidelined = (chart.filter(pl.col("depth_rank") == 1)
                      .join(hurt, on="player_id", how="inner")
                      .select(["chart_team", "chart_pos"])
                      .with_columns(pl.lit(True).alias("open_job")))
    if not sidelined.height:
        return board.with_columns(pl.lit(1.0).alias("vacancy_mult"))

    b = board
    if "depth_rank" not in b.columns:
        b = b.join(chart.drop(["chart_name", "chart_pos"]), on="player_id", how="left")
    b = b.join(chart.select(["player_id", "chart_pos"]), on="player_id", how="left")
    b = b.join(sidelined, left_on=["chart_team", "chart_pos"],
               right_on=["chart_team", "chart_pos"], how="left")

    lift = pl.lit(1.0)
    for pos, (d2, d3) in VACANCY_LIFT.items():
        step = (pl.when(pl.col("depth_rank") == 2).then(pl.lit(d2))
                .when(pl.col("depth_rank") >= 3).then(pl.lit(d3))
                .otherwise(pl.lit(1.0)))
        lift = pl.when(pl.col("position") == pos).then(step).otherwise(lift)

    b = b.with_columns(
        pl.when(pl.col("open_job").fill_null(False))
        .then(lift).otherwise(pl.lit(1.0)).alias("vacancy_mult")
    )

    out = b.with_columns(
        (pl.col("projected_points") * pl.col("vacancy_mult")).alias("projected_points"))
    for q in ("season_p20", "season_p50", "season_p80"):
        if q in out.columns:
            out = out.with_columns((pl.col(q) * pl.col("vacancy_mult")).alias(q))
    return out.drop([c for c in ("open_job",) if c in out.columns])
