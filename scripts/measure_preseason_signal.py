"""Does August tell you anything about September?

THE CLAIM BEING TESTED

Preseason is the only football in which the backups play, so it ought to say
something about which backup is next in line. That is a hypothesis, not a fact,
and the whole reason `data/preseason` exists is to make it checkable. Ranking
handcuffs on August yardage is exactly the sort of thing that feels obviously
right and turns out to be a survivorship artefact -- the same shape as the
soft-tissue recurrence flag in `features/injury`, which "worked" and pointed
backwards.

THE POPULATION

Every RB/WR/TE who touched the ball in a preseason game and was NOT his team's
first-teamer at his position on the week-one depth chart. That last clause is
the whole point: nobody needs a model to tell them about the starters, and
including them would make any feature look brilliant, because the starter both
plays with the ones and scores all season.

THE LABEL

His actual PPR points that regular season, and whether he finished startable
(RB top-30, WR top-42, TE top-14 -- roughly what a 12-team league starts).
Nobody is dropped for being cut or hurt; a backup who never played scores zero
and belongs in the sample. Dropping him is how you accidentally measure the
survivors.

THE CONTROL

Depth rank, which is free and already known. A feature that only recovers "he
is the RB2 rather than the RB4" has added nothing, so everything is reported
inside a rank stratum as well as across it.

THE SECOND TEST

What was actually asked: when the starter goes down, does August identify who
steps up? Same population, restricted to backups whose team's rank-1 at that
position missed four or more games. Smaller, noisier, and much closer to the
decision.
"""
import sys
from pathlib import Path

# The repo root, found from this file rather than written down. A hardcoded
# absolute path leaks whoever wrote it and breaks for everybody else.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

os.environ.setdefault("NFLREADPY_CACHE", "filesystem")
os.environ.setdefault("NFLREADPY_CACHE_DURATION", "86400")

import nflreadpy as nfl
import polars as pl

from fantasyedge.data import preseason as ps

# 2020 had no preseason at all.
SEASONS = [2016, 2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025]
POSITIONS = ("RB", "WR", "TE")
STARTABLE = {"RB": 30, "WR": 42, "TE": 14}
OUT_LONG = 4          # games the starter must miss for the second test

# ESPN and nflverse disagree about four clubs, and silently: an unmatched
# abbreviation does not error, it just drops the row on an inner join and takes
# the Rams and the Commanders out of the sample without saying so.
TEAM_FIX = {"WSH": "WAS", "LAR": "LA", "STL": "LA", "SD": "LAC", "OAK": "LV"}


def _team(col: str) -> pl.Expr:
    """Canonical franchise key. Applied to BOTH sides, so that a 2016 chart
    saying SD and an ESPN box score saying LAC meet in the middle instead of
    quietly failing to join."""
    return pl.col(col).replace(TEAM_FIX).alias("team")


def _spearman(a: pl.Series, b: pl.Series) -> float | None:
    d = pl.DataFrame({"a": a, "b": b}).drop_nulls()
    if d.height < 20:
        return None
    r = d.select(pl.corr(pl.col("a").rank(), pl.col("b").rank())).item()
    return round(r, 3) if r is not None else None


def season_frame(year: int) -> pl.DataFrame:
    """One row per preseason skill player, with his eventual season attached."""
    pre = ps.roles(year)
    if not pre.height or "player_id" not in pre.columns:
        return pl.DataFrame()
    pre = (pre.filter(pl.col("position").is_in(list(POSITIONS))
                      & pl.col("player_id").is_not_null())
              .select(["player_id", "player", "position", "team", "plays",
                       "plays_with_ones", "plays_with_qb1", "touches",
                       "targets", "points"])
              .rename({"points": "pre_points", "plays": "pre_plays",
                       "touches": "pre_touches", "targets": "pre_targets"})
              .with_columns(_team("team")))

    # HOW MUCH FIRST-TEAM WORK EXISTED TO BE HAD. Teams differ enormously in
    # how much they play the starters in August -- some quarterbacks never take
    # a preseason snap -- so a raw count punishes a good backup for his club's
    # caution. The share of his own team's first-team touches does not.
    pre = pre.with_columns(
        pl.col("plays_with_qb1").sum().over("team").alias("_team_qb1")
    ).with_columns(
        pl.when(pl.col("_team_qb1") > 0)
          .then(pl.col("plays_with_qb1") / pl.col("_team_qb1"))
          .otherwise(None).alias("qb1_team_share"))
    if not pre.height:
        return pl.DataFrame()

    # Entering-season depth rank: the week-one chart, which is published before
    # anyone has played a down that counts.
    dc = nfl.load_depth_charts(seasons=[year])
    rank_col = "depth_team" if "depth_team" in dc.columns else "pos_rank"
    pos_col = "position" if "position" in dc.columns else "pos_abb"
    if "week" in dc.columns:
        dc = dc.filter(pl.col("week") == 1)
    if "game_type" in dc.columns:
        dc = dc.filter(pl.col("game_type") == "REG")
    dc = (dc.filter(pl.col(pos_col).is_in(list(POSITIONS))
                    & pl.col("gsis_id").is_not_null())
            .select([pl.col("gsis_id").alias("player_id"),
                     pl.col(rank_col).cast(pl.Int32, strict=False)
                       .alias("depth_rank")])
            .drop_nulls()
            .group_by("player_id").agg(pl.col("depth_rank").min()))

    # THE POSITIONAL FINISH IS RANKED OVER EVERY PLAYER IN THE LEAGUE, not
    # over the preseason population. Ranking inside the sample would say a man
    # finished RB18 because the seventeen backs ahead of him are the only ones
    # who also played in August -- and the starters, who did not, are exactly
    # the men he has to beat to be startable.
    st = nfl.load_player_stats(seasons=[year])
    id_col = "player_id" if "player_id" in st.columns else "gsis_id"
    st = st.filter(pl.col("season_type") == "REG") if "season_type" in st.columns else st
    reg = (st.select([pl.col(id_col).alias("player_id"),
                      pl.col("fantasy_points_ppr").alias("pts"),
                      pl.col("position").alias("_pos")])
             .drop_nulls(["player_id"])
             .group_by(["player_id", "_pos"])
             .agg([pl.col("pts").sum().alias("reg_points"),
                   pl.len().cast(pl.Int32).alias("reg_games")])
             .with_columns(
                 pl.col("reg_points").rank("ordinal", descending=True)
                   .over("_pos").cast(pl.Int32).alias("pos_finish"))
             .drop("_pos")
             .unique(subset=["player_id"], keep="first"))

    d = (pre.join(dc, on="player_id", how="left")
            .join(reg, on="player_id", how="left")
            .with_columns([
                pl.col("reg_points").fill_null(0.0),
                pl.col("reg_games").fill_null(0),
                # Never appeared in a regular-season box score: he finished
                # behind everyone, which is a number, not a missing value.
                pl.col("pos_finish").fill_null(9999),
                # Off the chart entirely is deeper than rank 3, not missing.
                pl.col("depth_rank").fill_null(4),
                pl.lit(year).alias("season"),
            ]))

    bar = pl.lit(0)
    for pos, n in STARTABLE.items():
        bar = pl.when(pl.col("position") == pos).then(n).otherwise(bar)
    return d.with_columns((pl.col("pos_finish") <= bar).alias("startable"))


def starter_absence(year: int) -> pl.DataFrame:
    """Per team-position, how many games the week-one first-teamer missed."""
    dc = nfl.load_depth_charts(seasons=[year])
    rank_col = "depth_team" if "depth_team" in dc.columns else "pos_rank"
    pos_col = "position" if "position" in dc.columns else "pos_abb"
    team_col = "club_code" if "club_code" in dc.columns else "team"
    if "week" in dc.columns:
        dc = dc.filter(pl.col("week") == 1)
    if "game_type" in dc.columns:
        dc = dc.filter(pl.col("game_type") == "REG")
    ones = (dc.filter(pl.col(pos_col).is_in(list(POSITIONS))
                      & (pl.col(rank_col).cast(pl.Int32, strict=False) == 1)
                      & pl.col("gsis_id").is_not_null())
              .select([_team(team_col),
                       pl.col(pos_col).alias("position"),
                       pl.col("gsis_id").alias("starter_id")])
              .unique(subset=["team", "position"]))

    st = nfl.load_player_stats(seasons=[year])
    id_col = "player_id" if "player_id" in st.columns else "gsis_id"
    st = st.filter(pl.col("season_type") == "REG") if "season_type" in st.columns else st
    played = (st.select([pl.col(id_col).alias("starter_id")])
                .drop_nulls().group_by("starter_id")
                .agg(pl.len().cast(pl.Int32).alias("starter_games")))
    total = int(st["week"].max() or 17)
    return (ones.join(played, on="starter_id", how="left")
                .with_columns(
                    (total - pl.col("starter_games").fill_null(0))
                    .clip(0, None).alias("starter_missed")))


def quartiles(d: pl.DataFrame, col: str, label: str) -> None:
    """Mean outcome by quartile of a feature. The interpretable readout."""
    d = d.filter(pl.col(col).is_not_null())
    if d.height < 40:
        print(f"    {label}: too few rows ({d.height})")
        return
    q = d.with_columns(
        ((pl.col(col).rank("ordinal") - 1) * 4 // pl.len()).alias("_q"))
    rows = (q.group_by("_q").agg([
        pl.len().alias("n"),
        pl.col(col).mean().round(1).alias("feature"),
        pl.col("reg_points").mean().round(1).alias("reg_points"),
        pl.col("startable").mean().round(3).alias("startable_rate"),
    ]).sort("_q"))
    print(f"    {label}")
    for r in rows.iter_rows(named=True):
        print("      Q%d  n=%-4d  %-6s mean=%-7s  %5.1f pts   %4.1f%% startable"
              % (r["_q"] + 1, r["n"], col, r["feature"], r["reg_points"],
                 100 * r["startable_rate"]))


def main() -> None:
    frames = []
    for y in SEASONS:
        f = season_frame(y)
        if f.height:
            frames.append(f)
        print("%d  %d preseason skill players" % (y, f.height), flush=True)
    if not frames:
        print("no data")
        return
    cols = sorted(set.intersection(*(set(f.columns) for f in frames)))
    d = pl.concat([f.select(cols) for f in frames], how="vertical")

    print("\n%d player-seasons, %d seasons" % (d.height, d["season"].n_unique()))
    print("\nEVERYONE, by entering depth rank")
    print(d.group_by("depth_rank").agg([
        pl.len().alias("n"),
        pl.col("reg_points").mean().round(1).alias("reg_points"),
        pl.col("startable").mean().round(3).alias("startable_rate"),
    ]).sort("depth_rank"))

    backs = d.filter(pl.col("depth_rank") >= 2)
    print("\nBACKUPS ONLY -- %d player-seasons" % backs.height)
    for feat in ("plays_with_qb1", "qb1_team_share", "pre_points",
                 "pre_touches", "pre_plays"):
        print("  spearman(%s, reg_points) = %s" %
              (feat, _spearman(backs[feat], backs["reg_points"])))

    # THE QUARTILES LIE ABOUT THE SHAPE. Three of four contain nothing but
    # zeros, because most backups never share a snap with the first team --
    # so what looks like a graded feature is mostly one bit. Say so plainly.
    print("\n  DID HE TOUCH THE BALL WITH THE FIRST TEAM AT ALL?")
    for name, sub in (("all backups", backs),
                      ("depth rank 2", backs.filter(pl.col("depth_rank") == 2)),
                      ("depth rank 3", backs.filter(pl.col("depth_rank") == 3)),
                      ("rank 4+/off chart",
                       backs.filter(pl.col("depth_rank") >= 4))):
        rows = (sub.with_columns((pl.col("plays_with_qb1") > 0).alias("_any"))
                   .group_by("_any").agg([
                       pl.len().alias("n"),
                       pl.col("reg_points").mean().round(1).alias("pts"),
                       pl.col("startable").mean().alias("rate"),
                   ]).sort("_any"))
        parts = []
        for r in rows.iter_rows(named=True):
            parts.append("%s n=%-4d %5.1f pts %4.1f%%"
                         % ("yes" if r["_any"] else "no ", r["n"], r["pts"],
                            100 * r["rate"]))
        print("    %-18s %s" % (name, "   |   ".join(parts)))

    print("\n  AMONG THE ONES WHO DID -- is more of it better?")
    got = backs.filter(pl.col("plays_with_qb1") > 0)
    quartiles(got, "qb1_team_share", "share of his team's first-team touches")
    quartiles(got, "plays_with_qb1", "raw first-team touches")

    print("\n  across all backups")
    quartiles(backs, "plays_with_qb1", "first-team involvement")
    quartiles(backs, "pre_points", "raw preseason points (the naive feature)")

    for rank in (2, 3):
        sub = backs.filter(pl.col("depth_rank") == rank)
        print("\n  within depth rank %d -- n=%d" % (rank, sub.height))
        quartiles(sub, "plays_with_qb1", "first-team involvement")
        quartiles(sub, "pre_points", "raw preseason points")

    # ---- the starter actually got hurt ----------------------------------
    abs_frames = []
    for y in SEASONS:
        a = starter_absence(y)
        if a.height:
            abs_frames.append(a.with_columns(pl.lit(y).alias("season")))
    if abs_frames:
        ab = pl.concat(abs_frames, how="vertical")
        hurt = (backs.join(ab, on=["season", "team", "position"], how="inner")
                     .filter(pl.col("starter_missed") >= OUT_LONG))
        print("\n\nTHE STARTER MISSED %d+ GAMES -- n=%d" % (OUT_LONG, hurt.height))
        for feat in ("plays_with_qb1", "qb1_team_share", "pre_points"):
            print("  spearman(%s, reg_points) = %s" %
                  (feat, _spearman(hurt[feat], hurt["reg_points"])))
        rows = (hurt.with_columns((pl.col("plays_with_qb1") > 0).alias("_any"))
                    .group_by("_any").agg([
                        pl.len().alias("n"),
                        pl.col("reg_points").mean().round(1).alias("pts"),
                        pl.col("startable").mean().alias("rate"),
                    ]).sort("_any"))
        for r in rows.iter_rows(named=True):
            print("    first-team work %s  n=%-4d  %5.1f pts  %4.1f%% startable"
                  % ("YES" if r["_any"] else "no ", r["n"], r["pts"],
                     100 * r["rate"]))
        quartiles(hurt.filter(pl.col("plays_with_qb1") > 0), "qb1_team_share",
                  "among those with any -- share of team first-team touches")
        quartiles(hurt, "pre_points", "raw preseason points")


if __name__ == "__main__":
    main()
