"""What is a man at depth-chart rank N actually worth, relative to rank 1?

BACKUP_DISCOUNT stops at "rank 4+", one flat value for everything from the
fourth man down. That is why a fullback listed sixth gets no demotion against
a market that already had him fourth: both land in the same bucket, the ratio
is 1.0, and nothing happens. This measures the tail instead of assuming it.

2025 weekly depth charts (the only season nflverse publishes ranks deeper than
three for) joined to that week's PPR points, per team-position-week.
"""
import sys
sys.path.insert(0, "/Users/rithviksingidi/Downloads/FantasyEdge")
import polars as pl, nflreadpy as nfl

FANTASY = ("QB", "RB", "WR", "TE", "FB")

d = nfl.load_depth_charts(seasons=[2025])
d = (d.filter(pl.col("pos_abb").is_in(list(FANTASY)) & pl.col("gsis_id").is_not_null())
      .with_columns(pl.col("dt").str.slice(0, 10).str.to_date().alias("day")))

ps = nfl.load_player_stats(seasons=[2025])
id_col = "player_id" if "player_id" in ps.columns else "gsis_id"
ps = (ps.filter(pl.col("season_type") == "REG") if "season_type" in ps.columns else ps)
ps = ps.select([pl.col(id_col).alias("gsis_id"), "week",
                pl.col("fantasy_points_ppr").alias("pts")]).drop_nulls()

sch = (nfl.load_schedules(seasons=[2025])
       .filter(pl.col("game_type") == "REG")
       .select(["week", "gameday"])
       .with_columns(pl.col("gameday").str.to_date().alias("gd"))
       .group_by("week").agg(pl.col("gd").min().alias("start")))

# Each chart snapshot belongs to the next week that starts after it.
rows = []
for w in sch.sort("week").iter_rows(named=True):
    week, start = w["week"], w["start"]
    snap = d.filter(pl.col("day") < start)
    if not snap.height:
        continue
    latest = snap.filter(pl.col("day") == snap["day"].max())
    # A fullback is behind the running backs, the same rule the board uses.
    deep = (latest.filter(pl.col("pos_abb") == "RB").group_by("team")
                  .agg(pl.col("pos_rank").max().alias("_backs")))
    latest = (latest.join(deep, on="team", how="left")
              .with_columns(
                  pl.when(pl.col("pos_abb") == "FB")
                  .then(pl.col("pos_rank") + pl.col("_backs").fill_null(2))
                  .otherwise(pl.col("pos_rank")).alias("rank"))
              .with_columns(pl.when(pl.col("pos_abb") == "FB").then(pl.lit("RB"))
                            .otherwise(pl.col("pos_abb")).alias("pos")))
    rows.append(latest.select(["team", "pos", "rank", "gsis_id"])
                      .with_columns(pl.lit(week).alias("week")))

chart = pl.concat(rows).unique(subset=["week", "gsis_id"], keep="first")
j = chart.join(ps, on=["gsis_id", "week"], how="left").with_columns(
    pl.col("pts").fill_null(0.0))

print(f"{j.height} player-weeks, weeks {j['week'].min()}-{j['week'].max()}\n")

for pos in ("RB", "WR", "TE", "QB"):
    sub = j.filter(pl.col("pos") == pos)
    top = sub.filter(pl.col("rank") == 1)["pts"].mean()
    print(f"{pos}  (rank 1 = {top:.2f} ppg)")
    out = []
    for rk in range(1, 11):
        g = sub.filter(pl.col("rank") == rk)
        if g.height < 30:
            continue
        m = g["pts"].mean()
        out.append((rk, m / top, g.height))
        print(f"    rank {rk:>2}  {m:5.2f} ppg   {m/top:5.3f} of rank 1   n={g.height}")
    print()
