"""The claim under test:

    "More mid players for one really good player is not fair, while the math
     might make you look like that."

Value totals are the math that makes it look fair. A stud has one big number;
three solid starters have three medium numbers that add to more. Every trade
calculator on the internet stops there and calls it a win.

It is not a win, because two of the three never enter your lineup. This checks
that the engine knows the difference, and that it scales the right way: giving
up the stud should look WORSE the more bodies you take back, not better.
"""
import sys; sys.path.insert(0, ".")
import polars as pl

import draft as D
from fantasyedge.league import LeagueSettings
from fantasyedge.trade import evaluate

S = LeagueSettings(n_teams=12)
board = D.board(S)


def ids(names):
    got = board.filter(pl.col("player_name").is_in(names))
    missing = set(names) - set(got["player_name"].to_list())
    if missing:
        raise SystemExit(f"not on the board: {missing}")
    return got["player_id"].to_list()


# The test only means anything under two conditions, and the first version of
# it had neither:
#
#   THE ROSTER IS FULL. With a spare bench slot the extra bodies cost nothing,
#   so there is no opportunity cost to find. Every real mid-season roster is
#   full; that is the whole premise.
#
#   THE PLAYERS COMING BACK ARE GOOD. Filler with negative value loses on both
#   scales and proves nothing. The claim is about a package that genuinely
#   ADDS UP -- that is what makes the math look fair.
#
# So build both conditions instead of hand-picking names and hoping.
# And the roster has to be a TEAM, not a pile. The first attempt took the top
# sixteen by value, which in this scoring is almost all running backs -- so
# receiving a quarterback filled a hole worth 200 points and the whole table
# measured that instead of the thing being tested.
ranked = (board.sort("vor", descending=True, nulls_last=True)
               .filter(pl.col("position").is_in(["RB", "WR", "TE", "QB"])))
picked: list[str] = []
# Starters first, in the shape the league actually requires.
for slot, count in S.lineup.items():
    if slot in ("FLEX", "SUPERFLEX", "K", "DST"):
        continue
    picked += (ranked.filter(pl.col("position") == slot)
                     .head(count)["player_id"].to_list())
# Then a plausible bench: best available regardless of position.
for pid in ranked["player_id"].to_list():
    if len(picked) >= S.roster_size:
        break
    if pid not in picked:
        picked.append(pid)
mine = board.filter(pl.col("player_id").is_in(picked))
counts = mine["position"].value_counts().sort("position")
print("roster shape:", {r["position"]: r["count"] for r in counts.iter_rows(named=True)})

STUD_ROW = mine.sort("vor", descending=True, nulls_last=True).head(1)
STUD_IDS = STUD_ROW["player_id"].to_list()
stud_vor = float(STUD_ROW["vor"][0])
print(f"roster: {mine.height} men at a {S.roster_size}-man limit (FULL)")
print(f"giving up: {STUD_ROW['player_name'][0]}  (vor {stud_vor:+.0f})\n")

# Players whose value genuinely stacks up against him, none of them on my team.
pool = (ranked.filter(~pl.col("player_id").is_in(mine["player_id"].to_list()))
                .filter(pl.col("vor") > stud_vor * 0.30)
                .head(6))
BACK = pool["player_name"].to_list()[:4]

print(f"{'you get back':<46}{'VALUE':>9}{'LINEUP':>9}{'gap':>8}  verdict")
print("-" * 84)

rows = []
for n in range(1, min(4, len(BACK)) + 1):
    pack = BACK[:n]
    v = evaluate(mine, STUD_IDS, ids(pack), S, board)
    d = v.as_dict()
    tot = float(board.filter(pl.col("player_name").is_in(pack))["vor"].sum())
    label = f"{n} for 1 (their vor {tot:+.0f}): " + ", ".join(p.split()[-1] for p in pack)
    verdict = ("looks fair, is NOT" if d["naive_value_delta"] > 0 >= d["delta_median"]
               else "win" if d["delta_median"] > 0 else "loss")
    print(f"{label:<44}{d['naive_value_delta']:>+9.0f}{d['delta_median']:>+9.0f}"
          f"{d['opportunity_gap']:>8.0f}  {verdict}")
    rows.append((n, d))

print()
best = max(rows, key=lambda r: r[1]["naive_value_delta"])
print(f"On VALUE TOTALS the best of these is taking {best[0]} players "
      f"({best[1]['naive_value_delta']:+.0f}).")
worst = min(rows, key=lambda r: r[1]["delta_median"])
print(f"On YOUR LINEUP the worst is taking {worst[0]} players "
      f"({worst[1]['delta_median']:+.0f}).")

deltas = [d["delta_median"] for _, d in rows]
gaps = [d["opportunity_gap"] for _, d in rows]
print()
print("Does taking MORE bodies get worse, as it should?",
      "yes" if deltas == sorted(deltas, reverse=True) else "NO — check this")
print("Does the value/lineup gap widen with each extra body?",
      "yes" if gaps == sorted(gaps) else "NO — check this")

for _, d in rows:
    if d["dropped"]:
        print(f"\ncuts forced at {d['roster_before']} -> {d['roster_after']}: "
              + ", ".join(p["player_name"] for p in d["dropped"]))
        break
