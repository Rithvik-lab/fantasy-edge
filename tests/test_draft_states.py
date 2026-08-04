"""What happens if you connect BEFORE, DURING, or AFTER the draft?

Synthetic ESPN payloads in each state, pushed through the real parsing and the
real endpoints. No live league needed to find out whether this breaks.
"""
import sys; sys.path.insert(0, ".")
import polars as pl
from fastapi.testclient import TestClient

import server.app as A
from fantasyedge.data import espn_draft

SWID = "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}"
TEAMS = [{"id": i, "name": f"Team {i}", "abbrev": f"T{i}",
          "owners": [SWID if i == 7 else "{OTHER}"]} for i in range(1, 13)]

SETTINGS = {
    "name": "Sunday Money",
    "size": 12,
    "rosterSettings": {"lineupSlotCounts": {
        "0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1, "17": 1, "20": 7}},
    "scoringSettings": {"scoringItems": [{"statId": 53, "points": 1.0}]},
    "draftSettings": {"pickOrder": [3, 9, 1, 12, 7, 5, 2, 11, 4, 8, 6, 10]},
}

# Real ESPN ids so the crosswalk join actually resolves.
ESPN_IDS = ["4429795", "4430807", "4426515", "3918298", "4262921",
            "4241389", "4360438", "4569618", "4426354", "4239996",
            "3116385", "4362628", "4432708", "4429025", "4047365"]


def payload(n_picks: int, in_progress: bool, drafted: bool, order=True):
    picks = []
    for i in range(n_picks):
        rnd, rp = i // 12 + 1, i % 12 + 1
        # snake
        team = SETTINGS["draftSettings"]["pickOrder"][
            rp - 1 if rnd % 2 else 12 - rp]
        picks.append({"playerId": int(ESPN_IDS[i % len(ESPN_IDS)]),
                      "teamId": team, "overallPickNumber": i + 1,
                      "roundId": rnd, "roundPickNumber": rp, "keeper": False})
    s = dict(SETTINGS)
    if not order:
        s = {**SETTINGS, "draftSettings": {}}
    return {"settings": s, "teams": TEAMS,
            "members": [{"id": SWID}],
            "draftDetail": {"drafted": drafted, "inProgress": in_progress,
                            "picks": picks}}


def run(label, pl_, expect_note=""):
    print(f"\n{'='*66}\n{label}\n{'='*66}")
    A.espn_draft.fetch = lambda *a, **kw: pl_          # stub the network
    A.STATE.__init__()
    c = TestClient(A.app)
    r = c.post("/api/espn/connect", json={"league_id": "1", "season": 2026,
                                          "swid": SWID, "espn_s2": "x"})
    if r.status_code != 200:
        print("  connect FAILED:", r.status_code, r.text[:200]); return
    st = r.json()
    print(f"  league        : {st['league_name']} | {st['describe']}")
    print(f"  phase         : {st['phase']}   slot_confirmed={st['slot_confirmed']}")
    for w in st.get("warnings", []): print(f"  WARN          : {w}")
    print(f"  my slot       : {st['my_slot']}   (truth: 5, team 7 is 5th in pickOrder)")
    print(f"  picks seen    : {st['picks_made']}")
    print(f"  now on clock  : R{st['round']}.{st['pick']} (#{st['overall']})  mine={st['on_the_clock']}")
    print(f"  my next pick  : {st['my_next_pick']}")
    s = c.get("/api/suggestions?n=3")
    if s.status_code != 200:
        print("  suggestions   : FAILED", s.status_code, s.text[:300]); return
    d = s.json()
    if not d.get("suggestions"):
        print("  suggestions   :", d.get("note")); return
    for x in d["suggestions"][:3]:
        print(f"    - {x['player_name']:22s} {x['position']:3s} "
              f"survive {x['p_survive']:>4.0%}")
    print(f"  gap           : {d['picks_until_next']}")
    if expect_note:
        print(f"  >>> {expect_note}")


run("BEFORE the draft — nothing picked yet, pickOrder published",
    payload(0, False, False))
run("BEFORE the draft — pickOrder NOT published (common until it locks)",
    payload(0, False, False, order=False))
run("DURING the draft — 30 picks in", payload(30, True, False))
run("AFTER the draft — all 192 picks in", payload(192, False, True))
