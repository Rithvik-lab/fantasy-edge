#!/usr/bin/env python
"""Live draft assistant. Run it from a terminal while you draft.

    python draft.py --slot 7                       # who to take now
    python draft.py --slot 7 --taken "Chase,Gibbs" # after picks come off
    python draft.py --slot 7 --mine "Bijan"        # your roster so far

Everything is name-matched loosely, so partial names work: "Jeff" finds
Justin Jefferson. Add each pick to --taken as the draft moves.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import polars as pl  # noqa: E402

from fantasyedge.data import market  # noqa: E402
from fantasyedge.draft.engine import DraftState, recommend  # noqa: E402
from fantasyedge.features import build as fb  # noqa: E402
from fantasyedge.league import LeagueSettings, picks_for_slot  # noqa: E402

# Blend weight validated on 976 player-seasons: 35% model / 65% market beat
# market alone by 1.45 rank spots. Model alone was worse than market.
MODEL_WEIGHT = 0.35


def projections(settings: LeagueSettings) -> pl.DataFrame:
    """Market board with a points projection attached."""
    try:
        m = market.fetch()
        market.save(m)
    except Exception:
        m = market.load()

    m = m.filter(pl.col("gsis_id").is_not_null())

    # Historical mean points by positional finish -- converts a rank into
    # points so replacement level and VOR are computable.
    hist = fb.load().filter(pl.col("season") >= 2021)
    curve = (
        hist.with_columns(
            pl.col("total_points").rank("ordinal", descending=True)
            .over(["season", "position"]).alias("pr")
        )
        .group_by(["position", "pr"])
        .agg(pl.col("total_points").mean().alias("proj"))
    )

    return (
        m.with_columns(pl.col("pos_rank").cast(pl.UInt32).alias("pr"))
        .join(curve, on=["position", "pr"], how="left")
        .rename({"gsis_id": "player_id", "market_name": "player_name",
                 "proj": "projected_points"})
        .filter(pl.col("projected_points").is_not_null())
        .select(["player_id", "player_name", "position",
                 "projected_points", "ecr", "sd"])
    )


def match(proj: pl.DataFrame, names: list[str]) -> list[str]:
    """Loose name matching so you can type fast under pressure."""
    ids, misses = [], []
    for raw in names:
        q = raw.strip().lower()
        if not q:
            continue
        hit = proj.filter(pl.col("player_name").str.to_lowercase().str.contains(q))
        if hit.height:
            ids.append(hit["player_id"][0])
            if hit.height > 1:
                print(f"  note: '{raw}' matched {hit.height}, using "
                      f"{hit['player_name'][0]}")
        else:
            misses.append(raw)
    if misses:
        print(f"  WARNING: no match for {misses} — check spelling")
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, required=True, help="your draft position")
    ap.add_argument("--teams", type=int, default=12)
    ap.add_argument("--taken", default="", help="comma-separated, everyone drafted")
    ap.add_argument("--mine", default="", help="comma-separated, your picks")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--risk", default="balanced",
                    choices=["conservative", "balanced", "aggressive"])
    a = ap.parse_args()

    s = LeagueSettings(n_teams=a.teams)
    proj = projections(s)

    taken = match(proj, a.taken.split(",")) if a.taken else []
    mine = match(proj, a.mine.split(",")) if a.mine else []
    taken = list(dict.fromkeys(taken + mine))

    state = DraftState(settings=s, my_slot=a.slot, drafted=taken, my_roster=mine)
    rnd, pick = state.on_the_clock()

    print()
    print("=" * 72)
    print(f" ROUND {rnd}  PICK {pick}   |   slot {a.slot} of {a.teams}   |   "
          f"{len(taken)} off the board")
    my_picks = picks_for_slot(a.slot, a.teams, s.n_rounds)
    nxt = [p for p in my_picks if p > len(taken)][:3]
    print(f" your next picks (overall): {nxt}")
    print("=" * 72)

    if state.my_roster:
        roster = proj.filter(pl.col("player_id").is_in(state.my_roster))
        print(" YOUR ROSTER: " + ", ".join(
            f"{r['player_name']}({r['position']})"
            for r in roster.iter_rows(named=True)))
        print("-" * 72)

    rec = recommend(state, proj, n=a.n, risk_tolerance=a.risk)
    if not rec.height:
        print(" no players available")
        return 1

    print(f"{'#':<3}{'PLAYER':<24}{'POS':<5}{'ECR':>7}{'VOR':>8}"
          f"{'SURVIVE':>9}{'SCORE':>8}")
    print("-" * 72)
    for i, r in enumerate(rec.iter_rows(named=True), 1):
        surv = r["p_survive"]
        tag = "GONE" if surv < 0.25 else ("risky" if surv < 0.6 else "likely")
        print(f"{i:<3}{r['player_name'][:23]:<24}{r['position']:<5}"
              f"{r['ecr']:>7.1f}{r['vor']:>8.1f}{surv:>8.0%} {tag:<6}"
              f"{r['score']:>7.1f}")

    print("-" * 72)
    gap = rec["picks_until_next"][0]
    print(f" {gap} picks until your next turn.")
    print(" SURVIVE = chance he lasts that long. Low = take him now.")
    print(" VOR = points above the worst startable player at his position.")
    print()
    print(" next: add picks to --taken, add yours to --mine, rerun")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
