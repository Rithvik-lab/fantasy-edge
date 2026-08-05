"""Does the suggester find trades, and are they offerable?

Two things have to be true at once and they pull against each other: the deal
has to win on our simulated lineup, and it has to LOOK like a win on theirs.
Optimising only the first produces offers nobody accepts.
"""
import sys; sys.path.insert(0, ".")
import time
import polars as pl

import draft as D
from fantasyedge.league import LeagueSettings
from fantasyedge.trade import market, suggest

S = LeagueSettings(n_teams=12)
board = D.board(S)

# Deal a plausible league: snake the top 192 out to 12 teams.
pool = board.sort("projected_points", descending=True, nulls_last=True).head(192)
ids = pool["player_id"].to_list()
teams: dict[int, list[str]] = {i: [] for i in range(1, 13)}
for n, pid in enumerate(ids):
    rnd, seat = n // 12, n % 12
    tid = (seat if rnd % 2 == 0 else 11 - seat) + 1
    teams[tid].append(pid)

rosters = {t: board.filter(pl.col("player_id").is_in(v)) for t, v in teams.items()}
mine = rosters.pop(1)
print(f"my roster: {mine.height}   opponents: {len(rosters)}")

b = market.perceived(board)
mis = b.filter(pl.col("market_edge").is_not_null()).sort("market_edge", descending=True)
print("\n--- who the room misprices (value minus what his ADP normally buys) ---")
for label, df in [("UNDERPAID (buy)", mis.head(4)),
                  ("OVERPAID (sell)", mis.tail(4).reverse())]:
    print(f"  {label}")
    for r in df.iter_rows(named=True):
        print(f"    {r['player_name']:22} {r['position']:3} adp {r['ecr']:5.0f}  "
              f"vor {r['vor']:+6.0f}  vs curve {r['market_value']:+6.0f}  "
              f"edge {r['market_edge']:+6.0f}")

t0 = time.time()
offers = suggest.across_league(mine, rosters, board, S,
                               names={i: f"Team {i}" for i in range(2, 13)},
                               per_team=1, top=6)
el = time.time() - t0

print(f"\n--- {len(offers)} offerable trades, found in {el:.1f}s ---")
for o in offers:
    d = o.as_dict()
    print(f"\n  {d['team_name']}")
    print(f"    you give : {[r['player_name'] for r in d['give']]}")
    print(f"    you get  : {[r['player_name'] for r in d['get']]}")
    print(f"    you gain {d['our_gain']:+.0f} on your lineup "
          f"({d['win_probability']:.0%} of seasons)")
    print(f"    they read it as {d['their_gain']:+.0f} in their favour")
    print(f"    naive value scale would say {d['naive_delta']:+.0f}")
    if d["note"]:
        print(f"    {d['note']}")

if not offers:
    print("\n  none — every opponent is priced correctly against us, "
          "which is what a fair league looks like")
