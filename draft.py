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

from fantasyedge.data import espn, market, yahoo  # noqa: E402
from fantasyedge.draft.engine import DraftState, add_vor, recommend  # noqa: E402
from fantasyedge.draft.session import Session, grade_roster  # noqa: E402
from fantasyedge import config  # noqa: E402
from fantasyedge.features import build as fb  # noqa: E402
from fantasyedge.league import LeagueSettings, picks_for_slot  # noqa: E402
from fantasyedge.models import pergame_curve, rookie_risk  # noqa: E402

W = 78
_BOARD: pl.DataFrame | None = None
_PLATFORM = "espn"


def set_platform(name: str) -> None:
    """Choose whose ADP prices the board. Invalidates any cached board."""
    global _PLATFORM, _BOARD
    name = (name or "espn").lower()
    if name not in ("espn", "yahoo", "consensus"):
        raise ValueError(f"unknown platform {name!r}")
    if name != _PLATFORM:
        _BOARD = None
    _PLATFORM = name


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------

def _market() -> pl.DataFrame:
    """ADP from whichever platform this league drafts on.

    People draft off the board in front of them, and boards disagree. That is
    why ESPN replaced consensus here in the first place, and the same argument
    applies to Yahoo -- pricing a Yahoo room off ESPN's numbers would repeat
    the mistake. Raw ADP is NOT comparable across platforms (Yahoo runs about
    15 picks lower on average, a scale difference, not disagreement), which is
    exactly why everything downstream uses the RANK this produces.
    """
    src = {"espn": espn, "yahoo": yahoo}.get(_PLATFORM)
    if src is not None:
        try:
            m = src.fetch()
            src.save(m)
            print(f"  {_PLATFORM.upper()} ADP: {m.height} players, "
                  f"{src.match_report(m)['match_rate']}% matched")
            return m
        except Exception as exc:
            print(f"  {_PLATFORM} live fetch failed ({type(exc).__name__}); "
                  f"trying cached snapshot")
            try:
                return src.load()
            except Exception:
                pass
    m = market.fetch()
    market.save(m)
    return m


def board(settings: LeagueSettings) -> pl.DataFrame:
    """Full projection board: veterans, rookies, and season distributions."""
    global _BOARD
    if _BOARD is not None:
        return _BOARD

    m = _market().filter(pl.col("gsis_id").is_not_null())

    # Market rank -> what players ranked there have historically done. The
    # curve is keyed on PRE-season rank; keying it on where players finished
    # (as this did) conditions on the outcome and reads back an RB1 who plays
    # 16.4 games and never busts. See models/pergame_curve.
    curve = _curve()

    vets = (
        m.with_columns(pl.col("pos_rank").cast(pl.Int32).alias("pr"))
        .join(curve, on=["position", "pr"], how="left")
        .rename({"gsis_id": "player_id", "market_name": "player_name",
                 "c_points": "projected_points"})
        .filter(pl.col("projected_points").is_not_null())
        .select(["player_id", "player_name", "position", "projected_points",
                 "ecr", "sd"])
        .with_columns(pl.lit(False).alias("rookie"))
    )

    vets, rk = _merge_rookies(vets, _rookies())
    b = pl.concat([vets, rk], how="diagonal") if rk.height else vets

    # Rookie draft position is the least settled on the board, so widen it.
    b = b.with_columns(
        pl.when(pl.col("rookie").fill_null(False))
        .then(pl.struct(["ecr", "sd"]).map_elements(
            lambda r: rookie_risk.draft_sigma(r["ecr"], r["sd"]),
            return_dtype=pl.Float64))
        .otherwise(pl.col("sd")).alias("sd")
    )

    b = _scoring_mix(b)
    b = rookie_risk.apply(b)          # discount the projection ...
    b = _season_distribution(b)
    b = rookie_risk.apply_floor(b)    # ... and drop the floor further still
    _BOARD = add_vor(b, settings)
    return _BOARD


def _name_key() -> pl.Expr:
    """Normalised name, for matching a player across two ID systems."""
    return (
        pl.col("player_name").str.to_lowercase()
        .str.replace_all(r"\b(jr|sr|ii|iii|iv|v)\.?$", "")
        .str.replace_all(r"[^a-z ]", "")
        .str.strip_chars()
    )


def _merge_rookies(vets: pl.DataFrame,
                   rk: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Fold the rookie class into the market board without duplicating anyone.

    `rookies.parquet` carries nflverse PLACEHOLDER ids for an unsigned class
    ("LOV121782"), while the ESPN crosswalk carries real gsis ids. Matching on
    id therefore never fired, and 45 players sat on the board twice -- once at
    their real ESPN price and once at a fabricated one:

        Jeremiyah Love    real ecr  16    phantom ecr  28.4
        Ja'Kobi Lane      real ecr 178    phantom ecr  54.8

    That is worse than cosmetic. The engine could recommend the phantom copy
    as a bargain, and the extra bodies inflated every position pool -- which
    sets replacement level, and therefore every VOR on the board.

    ESPN's row wins, because it carries the price the league actually drafts
    from. All the rookie half contributes is the flag.
    """
    if not rk.height:
        return vets.with_columns(pl.col("rookie").fill_null(False)), rk

    # Name only, not name+position: the two sources disagree on position for
    # tweeners (ESPN had Max Bredeson at RB, the rookie table at TE), and a
    # position mismatch was letting the duplicate back through. ESPN's
    # position is the one the league scores by, so it wins.
    key = ["_k"]
    vets = vets.with_columns(_name_key().alias("_k"))
    rk = rk.with_columns(_name_key().alias("_k"))

    priced = rk.select(key).unique().with_columns(pl.lit(True).alias("_rk"))
    vets = (
        vets.join(priced, on=key, how="left")
        .with_columns(pl.col("_rk").fill_null(False).alias("rookie"))
        .drop("_rk", "_k")
    )
    # Whoever ESPN does not price stays a rookie row with a synthetic ADP.
    rk = rk.join(vets.with_columns(_name_key().alias("_k")).select(key),
                 on=key, how="anti").drop("_k")
    return vets, rk


def _rookies() -> pl.DataFrame:
    """Rookies, absent from the market board because they have no NFL history."""
    p = Path("data/processed/rookie_projections_2026.parquet")
    if not p.exists():
        return pl.DataFrame()
    r = pl.read_parquet(p).filter(pl.col("gsis_id").is_not_null())
    if not r.height:
        return pl.DataFrame()
    # A rookie ESPN does not price is, by revealed preference, a late pick --
    # so the synthetic board starts after the rounds ESPN does cover rather
    # than at pick 24. Anyone ESPN *does* price keeps their real ADP and never
    # reaches this function's output (see `_merge_rookies`).
    return (
        r.with_columns([
            (pl.col("projected_points").rank("ordinal", descending=True)
             .cast(pl.Float64) * 2.2 + 120.0).alias("ecr"),
            pl.lit(None, dtype=pl.Float64).alias("sd"),
            pl.lit(True).alias("rookie"),
            pl.col("projected_points").cast(pl.Float64),
        ])
        .rename({"gsis_id": "player_id"})
        .select(["player_id", "player_name", "position", "projected_points",
                 "ecr", "sd", "rookie"])
    )


def _scoring_mix(b: pl.DataFrame) -> pl.DataFrame:
    """Where a player's points come from, from last season.

    In full PPR this is the floor mechanism. Receptions are the most stable
    scoring event there is -- eight catches for 55 yards is 13.5 points with
    no touchdown. Touchdowns are the least stable and the least sticky year
    to year, so a player whose production leans on them has a lower floor at
    the same average.

    Two players with identical projections can be completely different
    assets: McBride takes 21% of his points from touchdowns and 40% from
    catches, while Goedert takes 36% from touchdowns and 32% from catches.
    Same position, same rough output, opposite risk shape.
    """
    try:
        f = fb.load().filter(pl.col("season") == config_last_season())
    except Exception:
        return b
    cols = [c for c in ("prior_td_share_of_points",
                        "prior_reception_share_of_points") if c in f.columns]
    if not cols:
        return b
    mix = f.select(["player_id"] + cols).rename({
        "prior_td_share_of_points": "td_share",
        "prior_reception_share_of_points": "rec_share",
    })
    return b.join(mix.unique(subset=["player_id"]), on="player_id", how="left")


def config_last_season() -> int:
    from fantasyedge import config as _c
    return _c.RAW_SEASON_END


def _curve() -> pl.DataFrame:
    """Pre-season rank -> historical outcomes. Built by models/pergame_curve."""
    cp = config.PROCESSED / "pergame_curve.parquet"
    if not cp.exists():
        pergame_curve.save(pergame_curve.build())
    return pl.read_parquet(cp)


def _season_distribution(b: pl.DataFrame) -> pl.DataFrame:
    """Season floor and ceiling, measured rather than composed.

    Simulating this from a rate distribution times a games distribution
    treats the two as independent, and they are not -- a back who loses his
    job scores less per game AND plays fewer of them, so the real season
    spread is wider than the product implies. Leave-one-season-out, composing
    them put only 50.6% of real seasons inside a band meant to hold 60%;
    reading the realised quantiles off the same pre-season rank puts 61.8%
    there, with the median beaten 51.0% of the time.

    `season_sim` is still the right tool for asking what-if questions about a
    specific rate and workload. It is the wrong tool for a board.
    """
    curve = _curve()
    need = ["c_season_p20", "c_season_p50", "c_season_p80"]
    if not set(need).issubset(curve.columns):
        return b
    ranked = (
        b.with_columns(
            pl.col("projected_points").rank("ordinal", descending=True)
            .over("position").cast(pl.Int32).alias("pr")
        )
        .join(curve.select(["position", "pr"] + need + ["c_games"]),
              on=["position", "pr"], how="left")
    )
    return ranked.rename({
        "c_season_p20": "season_p20", "c_season_p50": "season_p50",
        "c_season_p80": "season_p80", "c_games": "expected_games",
    }).with_columns(
        (pl.col("season_p80") - pl.col("season_p20")).alias("season_range")
    ).drop("pr")


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
    print(f"{'#':<3}{'PLAYER':<20}{'POS':<4}{'VOR':>7}{'FLOOR':>7}{'CEIL':>7}"
          f"{'TD%':>6}{'REC%':>6}{'SURVIVE':>9}{'SCORE':>7}")
    print("-" * W)
    for i, r in enumerate(rec.iter_rows(named=True), 1):
        surv = r["p_survive"]
        tag = "GONE" if surv < 0.25 else ("risky" if surv < 0.6 else "safe")
        nm = r["player_name"][:18] + (" R" if r.get("rookie") else "")
        fl = f"{r['floor']:>7.0f}" if r.get("floor") else "      -"
        ce = f"{r['ceiling']:>7.0f}" if r.get("ceiling") else "      -"
        td = f"{100*r['td_share']:>5.0f}%" if r.get('td_share') else "     -"
        rc = f"{100*r['rec_share']:>5.0f}%" if r.get('rec_share') else "     -"
        print(f"{i:<3}{nm:<20}{r['position']:<4}"
              f"{r['vor']:>7.1f}{fl}{ce}{td}{rc}{surv:>8.0%} {tag:<6}{r['score']:>6.1f}")
    print("-" * W)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_start(a) -> int:
    lineup = dict(QB=a.qb, RB=a.rb, WR=a.wr, TE=a.te, FLEX=a.flex, K=a.k, DST=a.dst)
    lineup = {k: v for k, v in lineup.items() if v > 0}
    s = Session.start(n_teams=a.teams, my_slot=a.slot,
                      points_per_reception=a.ppr, roster_size=a.rounds,
                      lineup=lineup, risk_tolerance=a.risk,
                      bench_tolerance=a.bench_risk)
    print(f"\nstarted: {s.settings.describe()}")
    print(f"your slot {a.slot} of {a.teams}")
    print(f"risk: starters {a.risk} | bench {a.bench_risk}")
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
    # Roster strength drives the simulation-derived volatility target.
    strength = None
    if s.my_ids:
        g = grade_roster(b.filter(pl.col("player_id").is_in(s.my_ids)),
                         s.settings, b)
        if g.get("score") and g.get("par"):
            # convert "% of par" into points-per-week vs league average
            strength = (g["score"] - 100) / 100 * g["par"] / 14.0

    rec = recommend(st, b, n=a.n, risk_tolerance=s.risk_tolerance,
                    bench_tolerance=s.bench_tolerance,
                    roster_strength=strength)
    if not rec.height:
        print(" nobody left")
        return 1
    extra = [c for c in ("rookie", "td_share", "rec_share") if c in b.columns]
    rec = rec.join(b.select(["player_id"] + extra), on="player_id", how="left")
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
    st.add_argument("--risk", default="combined",
                    choices=["safe", "combined", "aggressive"],
                    help="risk profile for STARTING lineup picks")
    st.add_argument("--bench-risk", default="aggressive",
                    choices=["safe", "combined", "aggressive"],
                    help="risk profile for BENCH picks (defaults aggressive: "
                         "a bench bust costs nothing, a bench hit starts)")
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
