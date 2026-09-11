"""What is an injury tag worth BEFORE the season, as opposed to on a Friday?

WHY THIS IS A SECOND SCRIPT AND NOT A SECOND COLUMN

`measure_status_availability.py` answers: given the tag in week W, did he play
in week W. That is the right question in October and the wrong one in August,
and the two answers are nowhere near each other. A Friday "questionable" is a
statement about Sunday. An August "questionable" is a statement about a season
that has not started, and using the weekly number for it took Ja'Marr Chase
from 15.1 expected games to 9.2 -- pricing a hamstring as half a year.

THE ANALOGUE THAT EXISTS

Nobody publishes a preseason injury report that goes back a decade. The closest
real object is the WEEK ONE report: it is filed the week of the opener, days
after the tag this is standing in for, and nflverse has it for every season.

THE POPULATION IS FIXED BEFORE ANY OF IT HAPPENS

Everyone in the top three at his position on his team's week-one depth chart --
a set decided by the chart, not by how the season went. Ranking on realised
outcomes is the survivorship trap this repo keeps rediscovering, and here it
would be especially inviting: condition on "players who mattered" and you have
quietly dropped everyone whose August injury ended their year.

THE LABEL

Share of his team's games in which he took an offensive snap. Snaps rather than
the box score, because a receiver who played and drew no targets has no
`player_stats` row and would otherwise count as absent.
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

SEASONS = list(range(2016, 2026))
FANTASY = ("QB", "RB", "WR", "TE")


def season_frame(year: int, ids: pl.DataFrame) -> pl.DataFrame:
    dc = nfl.load_depth_charts(seasons=[year])
    rank_col = "depth_team" if "depth_team" in dc.columns else "pos_rank"
    pos_col = "position" if "position" in dc.columns else "pos_abb"
    if "week" in dc.columns:
        dc = dc.filter(pl.col("week") == 1)
    if "game_type" in dc.columns:
        dc = dc.filter(pl.col("game_type") == "REG")
    chart = (dc.filter(pl.col(pos_col).is_in(list(FANTASY))
                       & pl.col("gsis_id").is_not_null())
               .select([pl.col("gsis_id").cast(pl.String),
                        pl.col(pos_col).alias("position"),
                        pl.col(rank_col).cast(pl.Int32, strict=False)
                          .alias("depth_rank")])
               .drop_nulls()
               .filter(pl.col("depth_rank") <= 3)
               .group_by("gsis_id")
               .agg([pl.col("position").first(),
                     pl.col("depth_rank").min()]))
    if not chart.height:
        return pl.DataFrame()

    inj = nfl.load_injuries(seasons=[year])
    kind = next((c for c in ("season_type", "game_type") if c in inj.columns),
                None)
    post = (pl.col(kind) != "POST").fill_null(True) if kind else pl.lit(True)
    wk1 = (inj.with_columns(pl.col("week").cast(pl.Int32, strict=False))
              .filter((pl.col("week") == 1) & post
                      & pl.col("gsis_id").is_not_null())
              .select([pl.col("gsis_id").cast(pl.String),
                       pl.col("report_status").alias("tag")])
              .drop_nulls()
              .unique(subset=["gsis_id"], keep="last"))

    snaps = nfl.load_snap_counts(seasons=[year])
    played = (snaps.filter((pl.col("game_type") == "REG")
                          & (pl.col("offense_snaps").fill_null(0) > 0))
                   .select([pl.col("pfr_player_id").cast(pl.String)
                              .alias("pfr_id"), "week"])
                   .unique()
                   .group_by("pfr_id")
                   .agg(pl.len().cast(pl.Int32).alias("games")))
    total = int(snaps.filter(pl.col("game_type") == "REG")["week"].max() or 17)

    return (chart.join(ids, on="gsis_id", how="left")
                 .join(played, on="pfr_id", how="left")
                 .join(wk1, on="gsis_id", how="left")
                 .with_columns([
                     pl.col("games").fill_null(0),
                     pl.col("tag").fill_null("none"),
                     pl.lit(year).alias("season"),
                     pl.lit(total).alias("team_games"),
                 ])
                 .with_columns(
                     (pl.col("games") / pl.col("team_games")).alias("share")))


def main() -> None:
    ids = (nfl.load_players()
           .select([pl.col("gsis_id").cast(pl.String),
                    pl.col("pfr_id").cast(pl.String)])
           .drop_nulls().unique(subset=["gsis_id"]))

    frames = []
    for y in SEASONS:
        try:
            f = season_frame(y, ids)
        except Exception as e:
            print(f"{y}: {type(e).__name__} {e}")
            continue
        if f.height:
            frames.append(f)
            print(f"{y}: {f.height} men in the top three at week one", flush=True)
    if not frames:
        print("no data")
        return
    d = pl.concat(frames, how="vertical")

    print(f"\n{d.height} player-seasons, {d['season'].n_unique()} seasons")
    print("\nSHARE OF THE SEASON PLAYED, BY WEEK-ONE TAG")
    base = d.filter(pl.col("tag") == "none")
    out = (d.group_by("tag").agg([
        pl.len().alias("n"),
        pl.col("share").mean().alias("share"),
    ]).filter(pl.col("n") >= 60).sort("share", descending=True))
    ref = float(base["share"].mean())
    print("  %-14s n=%-5d  played %5.1f%% of the season   (the baseline)"
          % ("no tag", base.height, 100 * ref))
    for r in out.iter_rows(named=True):
        if r["tag"] == "none":
            continue
        print("  %-14s n=%-5d  played %5.1f%%   -> multiplier %.2f"
              % (r["tag"], r["n"], 100 * r["share"], r["share"] / ref))

    print("\n  QUESTIONABLE at week one, by position")
    for pos in FANTASY:
        sub = d.filter(pl.col("position") == pos)
        b = sub.filter(pl.col("tag") == "none")
        q = sub.filter(pl.col("tag") == "Questionable")
        if q.height < 30 or not b.height:
            print("    %-4s n=%-4d  too few" % (pos, q.height))
            continue
        rb, rq = float(b["share"].mean()), float(q["share"].mean())
        print("    %-4s n=%-4d  %5.1f%% vs %5.1f%% untagged  -> multiplier %.2f"
              % (pos, q.height, 100 * rq, 100 * rb, rq / rb))

    print("\n  and for contrast, the SAME tag read as a weekly one would give")
    print("    Questionable  0.61 league-wide, 0.56 at running back")


if __name__ == "__main__":
    main()
