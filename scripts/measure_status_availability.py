"""What is an injury tag actually worth?

STATUS_AVAILABILITY is the table that turns ESPN's word into a number of
games, and every one of its values was asserted rather than measured. The
comment above it says questionable players "play the large majority of the
time" and then writes 0.75, which is a belief with a decimal point on it. It is
also now load-bearing: it is the only thing standing between a hurt player and
a price that says he is fine.

THE LABEL IS SNAPS, NOT STATS

Whether a man appears in `player_stats` is the wrong question -- a receiver who
played forty snaps and drew no targets has no row, so scoring availability off
the box score marks healthy players absent and drags every rate down. Snap
counts have a row for everyone who took the field.

WHAT THIS CANNOT ANSWER

The tag is a WEEKLY object: "questionable" means questionable for Sunday. This
measures exactly that -- given the tag in week W, did he play in week W. It
says nothing about what an August tag implies for a whole season, because
nflverse publishes no preseason injury report to measure it with.
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
FANTASY = ("QB", "RB", "WR", "TE", "FB")


def main() -> None:
    ids = (nfl.load_players()
           .select([pl.col("gsis_id").cast(pl.String),
                    pl.col("pfr_id").cast(pl.String)])
           .drop_nulls().unique(subset=["gsis_id"]))

    rows, every = [], []
    for year in SEASONS:
        try:
            inj = nfl.load_injuries(seasons=[year])
            snaps = nfl.load_snap_counts(seasons=[year])
        except Exception as e:
            print(f"{year}: {type(e).__name__}")
            continue
        if not inj.height or not snaps.height:
            continue

        # THE COLUMN THAT SAYS WHICH KIND OF GAME CHANGED NAME. Older seasons
        # carry `game_type`, newer ones `season_type`, and asking for the wrong
        # one raises rather than returning nothing -- which is the good case.
        # The bad case is the one this file's own history is full of: a filter
        # that silently drops most of the data.
        kind = next((c for c in ("season_type", "game_type")
                     if c in inj.columns), None)
        post = ((pl.col(kind) != "POST").fill_null(True) if kind
                else pl.lit(True))
        inj = (inj.with_columns([
                    pl.col("season").cast(pl.Int32, strict=False),
                    pl.col("week").cast(pl.Int32, strict=False)])
                 .filter(pl.col("gsis_id").is_not_null()
                         & pl.col("report_status").is_not_null()
                         & post)
                 .select([pl.col("gsis_id").cast(pl.String), "week",
                          pl.col("report_status").alias("status"),
                          pl.col("position")])
                 .filter(pl.col("position").is_in(list(FANTASY)))
                 .unique(subset=["gsis_id", "week"], keep="last"))

        played = (snaps.filter((pl.col("game_type") == "REG")
                              & (pl.col("offense_snaps").fill_null(0) > 0))
                       .select([pl.col("pfr_player_id").cast(pl.String)
                                  .alias("pfr_id"),
                                pl.col("week").cast(pl.Int32)])
                       .unique())

        d = (inj.join(ids, on="gsis_id", how="left")
                .drop_nulls("pfr_id")
                .join(played.with_columns(pl.lit(True).alias("played")),
                      on=["pfr_id", "week"], how="left")
                .with_columns([pl.col("played").fill_null(False),
                               pl.lit(year).alias("season")]))
        rows.append(d)
        # EVERY WEEK ANYONE PLAYED, not only the weeks somebody was tagged.
        # Built from `d` this was a list of appearances BY INJURED PLAYERS, so
        # looking a week ahead found no row for a man who had got healthy and
        # scored him as absent -- which read as a questionable player going
        # from 63% to 17% the following Sunday.
        every.append(played.with_columns(pl.lit(year).alias("season")))
        print(f"{year}: {d.height} tagged player-weeks", flush=True)

    if not rows:
        print("no data")
        return
    d = pl.concat(rows, how="vertical")
    played_all = pl.concat(every, how="vertical").with_columns(
        pl.lit(True).alias("hit"))

    print(f"\n{d.height} tagged player-weeks, {d['season'].n_unique()} seasons")
    print("\nPLAY RATE BY TAG — this is what the table should say")
    out = (d.group_by("status").agg([
        pl.len().alias("n"),
        pl.col("played").mean().alias("plays"),
    ]).filter(pl.col("n") >= 100).sort("plays", descending=True))
    for r in out.iter_rows(named=True):
        print("  %-14s n=%-6d  played %5.1f%%" % (r["status"], r["n"],
                                                  100 * r["plays"]))

    print("\n  by position, for the tag that matters most")
    q = (d.filter(pl.col("status") == "Questionable")
          .group_by("position").agg([
              pl.len().alias("n"),
              pl.col("played").mean().alias("plays")])
          .filter(pl.col("n") >= 100).sort("position"))
    for r in q.iter_rows(named=True):
        print("    %-4s n=%-6d  played %5.1f%%" % (r["position"], r["n"],
                                                   100 * r["plays"]))

    # HOW MANY GAMES DOES THE TAG COST, not what fraction of the rest of the
    # year. A weekly tag scaled across a whole season is the error that took
    # Ja'Marr Chase from 15.1 expected games to 9.2 for a hamstring: the tag is
    # a statement about Sunday, and its cost is measured in games, so it should
    # be SUBTRACTED from an expected-games total rather than multiplied into
    # it. This is how far forward it reaches.
    # A RAW LOSS RATE IS NOT THE COST OF THE TAG. This population is depth
    # charts full of rotational players who miss games for reasons that have
    # nothing to do with an injury report -- their steady-state play rate is
    # about 67%, so five weeks of anybody "loses" 1.6 games before a tag is
    # even mentioned. The number that matters is the MARGINAL one, so each man
    # is measured against himself: his own play rate that season in the weeks
    # he was not on the report at all.
    tagged = d.select(["pfr_id", "season", "week"]).unique().with_columns(
        pl.lit(True).alias("on_report"))
    own = (played_all.join(tagged, on=["pfr_id", "season", "week"], how="left")
           .with_columns(pl.col("on_report").fill_null(False)))
    # Weeks he played while untagged, over the untagged weeks he HAD. Dividing
    # by 17 instead is the same mistake one level down: a man tagged five times
    # has at most twelve clean weeks, so his ceiling would be 0.71 and every
    # marginal cost measured against it comes out too small.
    clean = (own.filter(~pl.col("on_report"))
                .group_by(["pfr_id", "season"])
                .agg(pl.len().cast(pl.Float64).alias("clean_games")))
    weeks_tagged = (d.select(["pfr_id", "season", "week"]).unique()
                     .group_by(["pfr_id", "season"])
                     .agg(pl.len().cast(pl.Float64).alias("n_tagged")))
    base = (clean.join(weeks_tagged, on=["pfr_id", "season"], how="left")
                 .with_columns(pl.col("n_tagged").fill_null(0.0))
                 .with_columns(
                     (pl.col("clean_games")
                      / (17.0 - pl.col("n_tagged")).clip(1.0, None))
                     .clip(0.0, 1.0).alias("own_base")))

    print("\nPLAY RATE IN THE NEXT FIVE WEEKS, BY TAG IN WEEK W")
    print("  (each man against his own untagged rate that season)")
    for status in ("Questionable", "Doubtful", "Out"):
        sub = d.filter(pl.col("status") == status).join(
            base, on=["pfr_id", "season"], how="left").drop_nulls("own_base")
        if sub.height < 100:
            continue
        rates, marg = [], 0.0
        for k in range(5):
            nxt = (sub.with_columns((pl.col("week") + k).alias("w"))
                      .join(played_all.rename({"week": "w"}),
                            on=["pfr_id", "w", "season"], how="left")
                      .with_columns(pl.col("hit").fill_null(False))
                      .filter(pl.col("w") <= 17))
            if not nxt.height:
                rates.append(float("nan"))
                continue
            r = float(nxt["hit"].mean())
            b = float(nxt["own_base"].mean())
            rates.append(r)
            marg += max(b - r, 0.0)
        print("  %-14s %s   marginal games lost: %.2f"
              % (status,
                 " ".join(f"W+{i}:{100*r:4.0f}%" for i, r in enumerate(rates)),
                 marg))

    print("\n  has it drifted? questionable by season")
    s = (d.filter(pl.col("status") == "Questionable")
          .group_by("season").agg([
              pl.len().alias("n"),
              pl.col("played").mean().alias("plays")]).sort("season"))
    for r in s.iter_rows(named=True):
        print("    %d  n=%-5d  %5.1f%%" % (r["season"], r["n"],
                                           100 * r["plays"]))


if __name__ == "__main__":
    main()
