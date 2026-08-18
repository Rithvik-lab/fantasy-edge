"""Does the trade engine price roster spots, or just add up value?

The case that decides it: a stud for three mid-tier starters. Value totals say
you win. A full bench says you do not, because two of the three never start and
you have to cut somebody to fit them.
"""
import sys; sys.path.insert(0, ".")
import polars as pl

import draft as D
from fantasyedge.league import LeagueSettings
from fantasyedge.trade import evaluate

S = LeagueSettings(n_teams=12)
board = D.board(S)


def team(names: list[str]) -> pl.DataFrame:
    got = board.filter(pl.col("player_name").is_in(names))
    missing = set(names) - set(got["player_name"].to_list())
    if missing:
        raise SystemExit(f"not on the board: {missing}")
    return got


# The verdict from the most recent `show`, so the check at the bottom can read
# what was actually printed rather than recomputing it.
LAST: dict = {}


def ids(names: list[str]) -> list[str]:
    """The engine works in ids; the test is written in names."""
    return board.filter(pl.col("player_name").is_in(names))["player_id"].to_list()


def show(label, v):
    d = v.as_dict()
    LAST.clear(); LAST.update(d)
    print(f"\n{'='*68}\n{label}\n{'='*68}")
    print(f"  give : {[r['player_name'] for r in d['give']]}")
    print(f"  get  : {[r['player_name'] for r in d['get']]}")
    print(f"  roster {d['roster_before']} -> {d['roster_after']}"
          + (f"   CUT: {[r['player_name'] for r in d['dropped']]}" if d["dropped"] else ""))
    print(f"\n  starting lineup   {d['before']['median']:.0f} -> {d['after']['median']:.0f}"
          f"   ({d['delta_median']:+.0f})")
    print(f"  floor             {d['before']['floor']:.0f} -> {d['after']['floor']:.0f}"
          f"   ({d['delta_floor']:+.0f})")
    print(f"  helps you in      {d['win_probability']:.0%} of simulated seasons")
    print(f"\n  ON VALUE TOTALS   {d['naive_value_delta']:+.0f}   <- what every calculator says")
    print(f"  ON YOUR LINEUP    {d['delta_median']:+.0f}   <- what you actually get")
    print(f"  opportunity cost  {d['opportunity_gap']:.0f}")
    if d["note"]:
        print(f"\n  {d['note']}")


# A realistic full roster: 16 men, starters plus a normal bench.
MINE = ["Christian McCaffrey", "Jahmyr Gibbs", "Puka Nacua", "Ja'Marr Chase",
        "Trey McBride", "Lamar Jackson", "Chase Brown", "Jaxon Smith-Njigba",
        "Tetairoa McMillan", "Tucker Kraft", "Bo Nix", "Zach Charbonnet",
        "Khalil Shakir", "Jayden Higgins", "Cam Little", "Denver Broncos"]
mine = team([n for n in MINE if board.filter(pl.col("player_name") == n).height])
print(f"roster: {mine.height} players")

stud = "Christian McCaffrey"
three = ["Chuba Hubbard", "Jerry Jeudy", "Dallas Goedert"]
three = [n for n in three if board.filter(pl.col("player_name") == n).height][:3]

show("ONE-FOR-THREE — the stud out, three mid pieces in (bench is full)",
     evaluate(mine, ids([stud]), ids(three), S, board))

show("ONE-FOR-ONE — same stud, one comparable back",
     evaluate(mine, ids([stud]), ids(["Bijan Robinson"]), S, board))

show("THREE-FOR-ONE — the other direction: depth out, stud in",
     evaluate(mine, ids(three[:2] + ["Khalil Shakir"]), ids(["Bijan Robinson"]), S, board))
CONSOLIDATE = dict(LAST)

show("PURE ADD — give nothing, take a bench arm (should be ~free, not huge)",
     evaluate(mine, [], ids(["Jerry Jeudy"]), S, board))
PURE_ADD = dict(LAST)


# THE CLAIMS THIS FILE EXISTS TO CHECK, enforced rather than printed. A script
# that says "NO — check this" and exits zero is reported green by every runner
# there is, including mine, which is how the fairness check sat broken through
# a whole day of commits.
#
# The check used to be "value says lose, lineup says win" on whichever verdict
# happened to be last, which was the pure add -- and it passed only because
# subtracting two independently-noisy medians left a point or two of jitter on
# a trade whose true effect is exactly nothing. Paired, a pure add is 0.0 and
# the check failed. It was testing the noise.
#
# So both claims are stated on the case that carries them, and neither is a
# strict inequality against zero.
if not (CONSOLIDATE["opportunity_gap"] > 50):
    raise SystemExit(
        f"three-for-one: value totals {CONSOLIDATE['naive_value_delta']:+.0f} vs "
        f"lineup {CONSOLIDATE['delta_median']:+.0f} — the value scale is supposed "
        f"to overstate a consolidation badly, and here it does not")

if abs(PURE_ADD["delta_median"]) > 5 or PURE_ADD["naive_value_delta"] > -20:
    raise SystemExit(
        f"pure add: value totals {PURE_ADD['naive_value_delta']:+.0f}, lineup "
        f"{PURE_ADD['delta_median']:+.0f} — a below-replacement bench arm should "
        f"be worth roughly nothing to a starting lineup and a large negative on "
        f"the value scale")
