"""Every team in the league, read as a trading partner.

WHY A SCAN NEEDS THIS AND NOT JUST OFFERS

A list of offers answers "what should I send". It does not answer the question
people actually ask first, which is "who should I be talking to". Those are
different: the best offer in the league might be with the one manager who has
nothing you need, and the fourth-best might be with someone whose roster is the
mirror image of yours and who will take the call.

So this reads all twelve rosters the same way and looks for the SHAPE of a
trade rather than a specific one:

  STRENGTH   each team's starters against the pool of players who actually
             start at that position in this league. Comparative, because a
             240-point tight end is excellent in one league and ordinary in
             another.
  SURPLUS    men who are NOT in their starting lineup but who clear the last
             starting slot in the league. That is the only definition of spare
             that means anything -- a fourth receiver on a deep team is an
             asset; a fourth receiver on a shallow one is a starter.
  NEED       a slot where they are behind the league, or empty.

A fit is a surplus on one side meeting a need on the other, and it is priced in
points: what their best spare man would ADD to your lineup, replacing whoever
you are starting there now. That number is the reason to send the message.

Kicker and defence are excluded from every judgement here. Both curves are
close to flat, so "spare kicker" is not an asset and "weak at kicker" is not a
need -- including them produced fits that were arithmetic and nothing else.
"""

from __future__ import annotations

import polars as pl

from fantasyedge.draft.report import (
    LINEUP_ORDER, NOT_WORTH_FIXING, _league_starters, in_lineup_order,
    positional_strength,
)
from fantasyedge.draft.session import optimal_lineup
from fantasyedge.league import LeagueSettings

# A move has to change the lineup to be a move at all. The threshold is zero
# rather than a round number picked for looking sensible: whether +6 points is
# worth a message is a judgement, and the number is right there to make it
# with. A hand-set floor here only ever hid the fact.
MIN_UPGRADE = 0.0

TRADEABLE = tuple(p for p in LINEUP_ORDER
                  if p not in NOT_WORTH_FIXING and p != "FLEX")


def _startable(board: pl.DataFrame, settings: LeagueSettings) -> dict[str, float]:
    """The LAST man who starts anywhere in this league, per position.

    Replacement level, not the median of the startable pool. The median is the
    sixteenth-best receiver in football, and asking whether a bench player
    clears that returns "nobody in this league has anything spare" -- which is
    both false and useless. Clearing the last starting slot is what makes a man
    an asset: someone, somewhere, would put him in a lineup.
    """
    out = {}
    for pos in TRADEABLE:
        pool = _league_starters(board, settings, pos)
        out[pos] = float(pool.min() or 0.0) if pool.len() else 0.0
    return out


def profile(roster: pl.DataFrame, board: pl.DataFrame,
            settings: LeagueSettings, floor: dict[str, float]) -> dict:
    """One team: what it starts, what it is short of, and what it can spare."""
    if not roster.height:
        return {"strength": [], "surplus": [], "needs": [], "starters": 0.0,
                "worst": {}}

    starters, bench = optimal_lineup(roster, settings)
    strength = [r for r in positional_strength(roster, board, settings)
                if r["position"] not in NOT_WORTH_FIXING]

    # The weakest man you currently start at each position -- the one a trade
    # would actually replace. Not the average: a trade does not upgrade an
    # average, it takes one name out of the lineup and puts another in.
    worst: dict[str, float] = {}
    for r in starters.iter_rows(named=True):
        pos = r["position"]
        pts = float(r.get("projected_points") or 0.0)
        worst[pos] = min(worst.get(pos, pts), pts)
    # An unfilled slot is a starter worth zero, which is what it costs you.
    for pos, count in settings.lineup.items():
        if pos in ("FLEX", "SUPERFLEX") or pos in NOT_WORTH_FIXING:
            continue
        if sum(1 for r in starters.iter_rows(named=True)
               if r["position"] == pos) < count:
            worst[pos] = 0.0

    surplus = []
    for r in bench.iter_rows(named=True):
        pos = r["position"]
        if pos in NOT_WORTH_FIXING:
            continue
        pts = float(r.get("projected_points") or 0.0)
        if pts >= floor.get(pos, 0.0):
            surplus.append({
                "player_id": r["player_id"], "player_name": r["player_name"],
                "position": pos, "projected_points": round(pts, 1),
                # How far past the last startable man he is -- what makes him
                # spare rather than merely owned.
                "over_replacement": round(pts - floor.get(pos, 0.0), 1),
            })
    surplus.sort(key=lambda r: -r["over_replacement"])

    needs = in_lineup_order([r for r in strength if r["edge"] < 0])

    everyone = []
    for df, on in ((starters, True), (bench, False)):
        for r in df.iter_rows(named=True):
            if r["position"] in NOT_WORTH_FIXING:
                continue
            everyone.append({
                "player_id": r["player_id"], "player_name": r["player_name"],
                "position": r["position"],
                "projected_points": round(float(r.get("projected_points") or 0), 1),
                "starting": on,
            })

    return {
        "strength": in_lineup_order(strength),
        "surplus": surplus[:5],
        "players": everyone,
        "needs": [r["position"] for r in needs],
        "starters": round(float(starters["projected_points"].fill_null(0).sum())
                          if starters.height else 0.0, 1),
        "worst": worst,
    }


def _adds(candidates: list[dict], worst: dict[str, float],
          limit: int = 3, only_upgrades: bool = True) -> list[dict]:
    """What each man would ADD to the other lineup, in points.

    The whole scan in one function: their fourth receiver is only interesting
    if he beats my third, and by how much is the entire question.

    `only_upgrades` is the difference between the two directions. What I OFFER
    has to improve their lineup or there is no reason for them to read the
    message. What I could GET is worth listing either way -- a deal can still
    win on depth and on the weeks a starter is out, which the simulated verdict
    prices and this arithmetic cannot. So the number comes back attached,
    negative and all, instead of the man being dropped and the panel going
    blank next to an offer naming him.
    """
    out = []
    for p in candidates:
        gain = p["projected_points"] - worst.get(p["position"], 0.0)
        if gain > MIN_UPGRADE or not only_upgrades:
            out.append({**p, "adds": round(gain, 1),
                        "starts_for_you": gain > MIN_UPGRADE})
    out.sort(key=lambda r: -r["adds"])
    return out[:limit]


def line(theirs: dict, mine: dict, ups: list[dict],
         sell: list[dict]) -> str:
    """One sentence, computed -- never written and never guessed."""
    if not ups:
        return ("Nobody here improves your lineup — this roster is not the "
                "one to call.")
    # A spare man is the easy ask, so name him first when one qualifies.
    best = next((u for u in ups if not u.get("starting")), ups[0])
    where = "is on their bench and" if not best.get("starting") \
        else "starts for them, but"
    tail = ", which is your weakest slot" \
        if best["position"] in mine.get("needs", []) else ""
    line_one = (f"{best['player_name']} {where} would add about "
                f"{round(best['adds'])} points at {best['position']}{tail}.")
    if sell:
        line_one += (f" They are short at {sell[0]['position']} — "
                     f"{sell[0]['player_name']} is what they would want back.")
    return line_one


def scan(mine: pl.DataFrame, rosters: dict[int, pl.DataFrame],
         board: pl.DataFrame, settings: LeagueSettings,
         names: dict[int, str] | None = None,
         my_team_id: int | None = None) -> dict:
    """Read the whole league at once. Yours comes back beside the others."""
    names = names or {}
    floor = _startable(board, settings)
    me = profile(mine, board, settings, floor)

    teams = []
    for tid, roster in rosters.items():
        p = profile(roster, board, settings, floor)
        # BOTH SIDES ARE THE SAME QUESTION, so both are asked the same way:
        # which of these men would change the other lineup, and by how much.
        #
        # The first version asked only about SPARE men -- their bench against
        # my starters -- and came back empty for all eleven teams, in a league
        # where three of them will trade with me this month. In an 18-man
        # league nobody's bench beats a real starter; the trade that happens is
        # a starter from strength for a starter at a need, and restricting the
        # search to spare parts cannot see it. Whether a man is spare is still
        # reported, because it says how hard the ask will be.
        ups = _adds(p["players"], me["worst"])
        theirs_want = _adds(me["players"], p["worst"])
        teams.append({
            "team_id": int(tid),
            "team_name": names.get(tid, f"Team {tid}"),
            "starters": p["starters"],
            "strength": p["strength"],
            "needs": p["needs"],
            "surplus": p["surplus"],
            "get_from_them": ups,
            "they_want_from_you": theirs_want,
            # A trade needs both halves, so the fit is the smaller of them.
            # Adding them let one enormous side carry a partner who wants
            # nothing I have, which is precisely the manager not to call.
            "fit": round(min(sum(u["adds"] for u in ups),
                             sum(u["adds"] for u in theirs_want)), 1),
            "note": line(p, me, ups, theirs_want),
        })

    teams.sort(key=lambda t: -t["fit"])
    ranked = sorted(teams + [{"team_id": my_team_id, "starters": me["starters"]}],
                    key=lambda t: -t["starters"])
    where = {t.get("team_id"): i + 1 for i, t in enumerate(ranked)}
    for t in teams:
        t["rank"] = where.get(t["team_id"])

    return {
        "me": {**me, "team_id": my_team_id, "rank": where.get(my_team_id),
               "team_name": names.get(my_team_id or -1, "My team")},
        "teams": teams,
    }
