#!/usr/bin/env python
"""Interactive draft assistant.

Configure once, then work pick by pick. State persists between commands, so a
closed terminal costs nothing.

    python draft.py start --teams 12 --slot 7 --ppr 1.0
    python draft.py suggest              # 3 best options right now
    python draft.py take "McCaffrey"     # you drafted him
    python draft.py pick "Chase"         # someone else did
    python draft.py roster               # your team and its grade
    python draft.py board --pos RB       # best available
    python draft.py undo
    python draft.py status

Names match loosely: "Jeff" finds Justin Jefferson.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import polars as pl  # noqa: E402

from fantasyedge.data import market  # noqa: E402
from fantasyedge.draft.engine import DraftState, add_vor, recommend  # noqa: E402
from fantasyedge.draft.session import Session, grade_roster  # noqa: E402
from fantasyedge.features import build as fb  # noqa: E402
from fantasyedge.league import LeagueSettings, picks_for_slot  # noqa: E402
from fantasyedge.models import season_sim  # noqa: E402

W = 78
_BOARD: pl.DataFrame | None = None


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------

def board(settings: LeagueSettings) -> pl.DataFrame:
    """Full projection board: veterans, rookies, and season distributions."""
    global _BOARD
    if _BOARD is not None:
        return _BOARD

    try:
        m = market.fetch()
        market.save(m)
    except Exception:
        m = market.load()
    m = m.filter(pl.col("gsis_id").is_not_null())

    hist = fb.load().filter(pl.col("season") >= 2021)
    curve = (
        hist.with_columns(
            pl.col("total_points").rank("ordinal", descending=True)
            .over(["season", "position"]).alias("pr")
        )
        .group_by(["position", "pr"]).agg(pl.col("total_points").mean().alias("proj"))
    )

    vets = (
        m.with_columns(pl.col("pos_rank").cast(pl.UInt32).alias("pr"))
        .join(curve, on=["position", "pr"], how="left")
        .rename({"gsis_id": "player_id", "market_name": "player_name",
                 "proj": "projected_points"})
        .filter(pl.col("projected_points").is_not_null())
        .select(["player_id", "player_name", "position", "projected_points",
                 "ecr", "sd"])
        .with_columns(pl.lit(False).alias("rookie"))
    )

    b = pl.concat([vets, _rookies()], how="diagonal")
    b = _season_distribution(b)
    _BOARD = add_vor(b, settings)
    return _BOARD


def _rookies() -> pl.DataFrame:
    """Rookies, absent from the market board because they have no NFL history."""
    p = Path("data/processed/rookie_projections_2026.parquet")
    if not p.exists():
        return pl.DataFrame()
    r = pl.read_parquet(p).filter(pl.col("gsis_id").is_not_null())
    if not r.height:
        return pl.DataFrame()
    return (
        r.with_columns([
            (pl.col("projected_points").rank("ordinal", descending=True)
             .cast(pl.Float64) * 2.2 + 24.0).alias("ecr"),
            pl.lit(4.5).alias("sd"),
            pl.lit(True).alias("rookie"),
            pl.col("projected_points").cast(pl.Float64),
        ])
        .rename({"gsis_id": "player_id"})
        .select(["player_id", "player_name", "position", "projected_points",
                 "ecr", "sd", "rookie"])
    )


def _season_distribution(b: pl.DataFrame) -> pl.DataFrame:
    """Simulated season floor and ceiling, folding in availability."""
    cp = Path("data/processed/pergame_curve.parquet")
    if not cp.exists():
        return b
    curve = pl.read_parquet(cp)
    ranked = (
        b.with_columns(
            pl.col("projected_points").rank("ordinal", descending=True)
            .over("position").cast(pl.Int32).alias("pr")
        )
        .join(curve, on=["position", "pr"], how="left")
        .filter(pl.col("c_q50").is_not_null())
    )
    if not ranked.height:
        return b
    sim = season_sim.simulate_frame(
        ranked.rename({"c_q20": "q20", "c_q50": "q50", "c_q80": "q80",
                       "c_games": "expected_games"}),
        n_sims=2000,
    )
    if not sim.height:
        return b
    return b.join(
        sim.select(["player_id", "season_p20", "season_p50", "season_p80",
                    "season_range"]),
        on="player_id", how="left")


def find(b: pl.DataFrame, name: str, taken: list[str]) -> dict | None:
    """Loose name lookup, preferring undrafted players."""
    q = name.strip().lower()
    hits = b.filter(pl.col("player_name").str.to_lowercase().str.contains(q))
    if not hits.height:
        return None
    free = hits.filter(~pl.col("player_id").is_in(taken))
    pool = free if free.height else hits
    if pool.height > 1:
        print(f"  '{name}' matched {pool.height}; using "
              f"{pool.sort('ecr')['player_name'][0]}")
    return pool.sort("ecr").head(1).to_dicts()[0]


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def header(s: Session) -> None:
    rnd, pick, overall = s.on_the_clock()
    turn = "  <<< YOUR PICK" if s.is_my_turn() else ""
    print("=" * W)
    print(f" ROUND {rnd}  PICK {pick}  (overall {overall}){turn}")
    print(f" {s.n_teams}-team | {s.points_per_reception} PPR | slot {s.my_slot}"
          f" | {len(s.picks)} drafted")
    print("=" * W)


def show_suggestions(rec: pl.DataFrame) -> None:
    print(f"{'#':<3}{'PLAYER':<21}{'POS':<4}{'ECR':>6}{'VOR':>7}"
          f"{'FLOOR':>7}{'CEIL':>7}{'SURVIVE':>9}{'SCORE':>7}")
    print("-" * W)
    for i, r in enumerate(rec.iter_rows(named=True), 1):
        surv = r["p_survive"]
        tag = "GONE" if surv < 0.25 else ("risky" if surv < 0.6 else "safe")
        nm = r["player_name"][:18] + (" R" if r.get("rookie") else "")
        fl = f"{r['floor']:>7.0f}" if r.get("floor") else "      -"
        ce = f"{r['ceiling']:>7.0f}" if r.get("ceiling") else "      -"
        print(f"{i:<3}{nm:<21}{r['position']:<4}{r['ecr']:>6.1f}"
              f"{r['vor']:>7.1f}{fl}{ce}{surv:>8.0%} {tag:<6}{r['score']:>6.1f}")
    print("-" * W)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_start(a) -> int:
    lineup = dict(QB=a.qb, RB=a.rb, WR=a.wr, TE=a.te, FLEX=a.flex, K=a.k, DST=a.dst)
    lineup = {k: v for k, v in lineup.items() if v > 0}
    s = Session.start(n_teams=a.teams, my_slot=a.slot,
                      points_per_reception=a.ppr, roster_size=a.rounds,
                      lineup=lineup, risk_tolerance=a.risk)
    print(f"\nstarted: {s.settings.describe()}")
    print(f"your slot {a.slot} of {a.teams}, risk {a.risk}")
    print(f"your picks: {picks_for_slot(a.slot, a.teams, a.rounds)[:6]} ...")
    print("\nnext: draft.py suggest")
    return 0


def cmd_suggest(a) -> int:
    s = Session.load()
    b = board(s.settings)
    header(s)
    if not s.is_my_turn():
        print(" not your pick — record picks with `pick NAME` until it is.\n")

    st = DraftState(settings=s.settings, my_slot=s.my_slot,
                    drafted=s.drafted_ids, my_roster=s.my_ids)
    rec = recommend(st, b, n=a.n, risk_tolerance=s.risk_tolerance)
    if not rec.height:
        print(" nobody left")
        return 1
    rec = rec.join(b.select(["player_id", "rookie"]), on="player_id", how="left")
    show_suggestions(rec)

    rr = rec["roster_risk"][0]
    if rr is not None:
        mood = "safe" if rr < 0.4 else ("volatile" if rr > 0.6 else "balanced")
        print(f" roster reads {mood} ({rr:.2f}) -> targeting "
              f"{rec['target_vol_pct'][0]:.2f} volatility")
    gap = rec["picks_until_next"][0]
    if gap is not None:
        print(f" {gap} picks until your next turn")
    print(f"\n take one:  draft.py take \"{rec['player_name'][0]}\"")
    return 0


def cmd_take(a) -> int:
    return _record(a.name, mine=True)


def cmd_pick(a) -> int:
    return _record(a.name, mine=False)


def _record(name: str, mine: bool) -> int:
    s = Session.load()
    b = board(s.settings)
    hit = find(b, name, s.drafted_ids)
    if not hit:
        print(f" no match for '{name}'")
        return 1
    if hit["player_id"] in s.drafted_ids:
        print(f" {hit['player_name']} already drafted")
        return 1

    p = s.add(hit["player_id"], hit["player_name"], hit["position"], mine)
    s.save()
    who = "YOU" if mine else "opponent"
    print(f" pick {p.overall}: {who} -> {p.player_name} ({p.position})")

    if mine:
        _print_grade(s, b)
    nxt = s.picks_until_mine()
    print(f" {nxt} picks until your next turn" if nxt else " draft complete")
    return 0


def _print_grade(s: Session, b: pl.DataFrame) -> None:
    roster = b.filter(pl.col("player_id").is_in(s.my_ids))
    g = grade_roster(roster, s.settings, b)
    if "error" in g:
        return
    print(f"\n TEAM: {g['players']} players | starters {g['starter_points']:.0f} pts"
          f" | score {g['score']:.0f}  (100 = par)")
    if g["season_floor"]:
        print(f"       season floor {g['season_floor']:.0f} / "
              f"ceiling {g['season_ceiling']:.0f}")
    if g["unfilled"]:
        print("       still need: "
              + ", ".join(f"{v}x{k}" for k, v in g["unfilled"].items()))


def cmd_roster(a) -> int:
    s = Session.load()
    b = board(s.settings)
    roster = b.filter(pl.col("player_id").is_in(s.my_ids))
    if not roster.height:
        print(" no players yet")
        return 0
    g = grade_roster(roster, s.settings, b)
    print("=" * W)
    print(f" YOUR TEAM — score {g['score']:.0f} (100 = par for this league)")
    print("=" * W)
    print(" STARTERS")
    for r in g["lineup"].iter_rows(named=True):
        print(f"   {r['position']:<4}{r['player_name'][:24]:<26}"
              f"{r['projected_points']:>7.0f} pts   VOR {r['vor']:>6.1f}")
    if g["bench"].height:
        print(" BENCH")
        for r in g["bench"].iter_rows(named=True):
            print(f"   {r['position']:<4}{r['player_name'][:24]:<26}"
                  f"{r['projected_points']:>7.0f} pts")
    print("-" * W)
    print(f" starters {g['starter_points']:.0f} pts | par {g['par']:.0f}"
          f" | VOR {g['starter_vor']:.0f}")
    if g["season_floor"]:
        print(f" season floor {g['season_floor']:.0f} / ceiling {g['season_ceiling']:.0f}")
    if g["risk_profile"] is not None:
        print(f" risk profile {g['risk_profile']:.2f}  (0 = safe, 1 = volatile)")
    if g["unfilled"]:
        print(" unfilled: " + ", ".join(f"{v}x{k}" for k, v in g["unfilled"].items()))
    return 0


def cmd_board(a) -> int:
    s = Session.load()
    b = board(s.settings)
    avail = b.filter(~pl.col("player_id").is_in(s.drafted_ids))
    if a.pos:
        avail = avail.filter(pl.col("position") == a.pos.upper())
    print(avail.sort("ecr").select(
        ["player_name", "position", "ecr", "projected_points", "vor",
         "season_p20", "season_p80"]).head(a.n))
    return 0


def cmd_undo(a) -> int:
    s = Session.load()
    p = s.undo()
    if not p:
        print(" nothing to undo")
        return 1
    s.save()
    print(f" removed pick {p['overall']}: {p['player_name']}")
    return 0


def cmd_status(a) -> int:
    s = Session.load()
    header(s)
    if s.picks:
        print(" recent picks:")
        for p in s.picks[-8:]:
            who = "YOU" if p["mine"] else "   "
            print(f"   {p['overall']:>3}  {who}  {p['player_name']} ({p['position']})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("start", help="configure a new draft")
    st.add_argument("--teams", type=int, default=12)
    st.add_argument("--slot", type=int, required=True)
    st.add_argument("--ppr", type=float, default=1.0)
    st.add_argument("--rounds", type=int, default=16)
    st.add_argument("--risk", default="balanced",
                    choices=["conservative", "balanced", "aggressive"])
    for pos, dflt in (("qb", 1), ("rb", 2), ("wr", 2), ("te", 1),
                      ("flex", 1), ("k", 1), ("dst", 1)):
        st.add_argument(f"--{pos}", type=int, default=dflt)
    st.set_defaults(fn=cmd_start)

    sg = sub.add_parser("suggest", help="best options right now")
    sg.add_argument("--n", type=int, default=3)
    sg.set_defaults(fn=cmd_suggest)

    tk = sub.add_parser("take", help="you drafted this player")
    tk.add_argument("name")
    tk.set_defaults(fn=cmd_take)

    pk = sub.add_parser("pick", help="someone else drafted this player")
    pk.add_argument("name")
    pk.set_defaults(fn=cmd_pick)

    rs = sub.add_parser("roster", help="your team and grade")
    rs.set_defaults(fn=cmd_roster)

    bd = sub.add_parser("board", help="best available")
    bd.add_argument("--pos", default=None)
    bd.add_argument("--n", type=int, default=15)
    bd.set_defaults(fn=cmd_board)

    sub.add_parser("undo", help="take back the last pick").set_defaults(fn=cmd_undo)
    sub.add_parser("status", help="where the draft is").set_defaults(fn=cmd_status)

    a = ap.parse_args()
    try:
        return a.fn(a)
    except FileNotFoundError as e:
        print(f" {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
