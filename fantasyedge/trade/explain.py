"""Why a trade is good or bad, in sentences, from the numbers that decided it.

Same rule as the draft explanations: nothing here is written by a language
model and nothing here is a heuristic bolted on afterwards. Every line is
computed from the same simulation that produced the verdict, so the reasoning
cannot drift away from the recommendation. If the text says your RB1 slot
improves by 22, the lineup simulation says so too.

The shape of the answer is deliberately two-sided. Almost no real trade is
purely good — you give up something to get something, and a tool that lists
only reasons to accept is a tool that has stopped thinking. The interesting
question is never "is this good" but "what am I actually paying".
"""

from __future__ import annotations

import polars as pl

from fantasyedge.draft.session import optimal_lineup
from fantasyedge.league import LeagueSettings

# Below this, a slot moved but not by enough to mention. Fantasy weeks swing
# far more than this; naming a four-point change as a reason is noise wearing
# the costume of insight.
MATERIAL = 8.0

POS_WORD = {"RB": "running back", "WR": "receiver", "TE": "tight end",
            "QB": "quarterback", "K": "kicker", "DST": "defence"}


def _slot_strength(roster: pl.DataFrame, settings: LeagueSettings) -> dict[str, float]:
    """Projected points contributed by each starting slot."""
    if not roster.height:
        return {}
    starters, _ = optimal_lineup(roster, settings)
    if not starters.height:
        return {}
    out: dict[str, float] = {}
    for r in starters.iter_rows(named=True):
        slot = r.get("slot") or r["position"]
        out[slot] = out.get(slot, 0.0) + float(r.get("projected_points") or 0.0)
    return out


def _depth_after(roster: pl.DataFrame, position: str,
                 settings: LeagueSettings) -> int:
    """How many bodies are left at a position once starters are accounted for."""
    have = roster.filter(pl.col("position") == position).height
    need = settings.lineup.get(position, 0)
    return have - need


def _unfilled(roster: pl.DataFrame, settings: LeagueSettings) -> list[str]:
    """Starting slots this roster cannot fill.

    A slot with nobody in it scores zero every week, which is a different kind
    of fact from "your WR2 got worse" and belongs above it.
    """
    if not roster.height:
        return list(s for s in settings.lineup if s not in ("FLEX", "SUPERFLEX"))
    starters, _ = optimal_lineup(roster, settings)
    have: dict[str, int] = {}
    for r in starters.iter_rows(named=True):
        have[r["position"]] = have.get(r["position"], 0) + 1
    return [slot for slot, count in settings.lineup.items()
            if slot not in ("FLEX", "SUPERFLEX") and count - have.get(slot, 0) > 0]


def _starters(roster: pl.DataFrame, settings: LeagueSettings) -> dict[str, dict]:
    """player_id -> his row, for whoever is in the best legal lineup."""
    if not roster.height:
        return {}
    starters, _ = optimal_lineup(roster, settings)
    return {r["player_id"]: r for r in starters.iter_rows(named=True)} \
        if starters.height else {}


def lineup_moves(before: pl.DataFrame, after: pl.DataFrame,
                 settings: LeagueSettings) -> list[dict]:
    """Your lineup card, slot by slot, before and after.

    WHY THIS REPLACED FOUR SEPARATE REASONS. A trade does not change one slot,
    it re-solves the whole lineup, and the old presentation reported the
    consequences as if they were independent gains:

        +52 your FLEX slot gets better
        +47 your WR1 slot gets better
        +15 your WR2 slot gets better
        -76 your RB2 slot gets worse

    Every line was true and the set of them reads like a bug -- one receiver
    arrived, so how did three slots improve? Because the slots are ranks, not
    people: the new man takes WR1, the old WR1 slides to WR2, that man slides
    to FLEX, and the back who left empties RB2. It is one cascade. Shown as a
    before/after card it explains itself and the numbers stop looking invented.
    """
    def card(roster: pl.DataFrame) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        if not roster.height:
            return out
        starters, _ = optimal_lineup(roster, settings)
        for r in starters.iter_rows(named=True):
            slot = r.get("slot") or r["position"]
            out.setdefault(slot, []).append({
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "position": r["position"],
                "points": float(r.get("projected_points") or 0.0),
            })
        for v in out.values():
            v.sort(key=lambda p: -p["points"])
        return out

    b, a = card(before), card(after)
    rows = []
    for slot in sorted(set(b) | set(a), key=lambda s: LINEUP_SORT(s)):
        was, now = b.get(slot, []), a.get(slot, [])
        for i in range(max(len(was), len(now))):
            x = was[i] if i < len(was) else None
            y = now[i] if i < len(now) else None
            if (x or {}).get("player_id") == (y or {}).get("player_id"):
                continue
            rows.append({
                "slot": slot,
                "out": x["player_name"] if x else None,
                "out_id": x["player_id"] if x else None,
                "out_points": round(x["points"], 1) if x else 0.0,
                "in": y["player_name"] if y else None,
                "in_id": y["player_id"] if y else None,
                "in_points": round(y["points"], 1) if y else 0.0,
                "position": (y or x or {}).get("position"),
                "delta": round((y["points"] if y else 0.0)
                               - (x["points"] if x else 0.0), 1),
            })
    return rows


# Lineup-card order for a slot label, so the card reads the way it is written.
_ORDER = ("QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX", "DST", "K")


def LINEUP_SORT(slot: str) -> tuple[int, str]:
    base = (slot or "").rstrip("0123456789") or slot
    return (_ORDER.index(base) if base in _ORDER else len(_ORDER), slot)


def promotions(before: pl.DataFrame, after: pl.DataFrame,
               settings: LeagueSettings, incoming: set[str]) -> list[dict]:
    """WHO STARTS INSTEAD. The question a trade actually turns on.

    Giving up a receiver does not cost you that receiver's points. It costs you
    the difference between him and whoever moves up -- which is why the same
    player is expensive to trade off a thin roster and nearly free off a deep
    one, and why "is there anyone worth starting behind him" is the question to
    ask before agreeing to anything.

    Computed by re-solving the lineup, not asserted: the men here are the ones
    who are in the best legal lineup afterwards and were not in it before, with
    the players arriving in the trade excluded -- they are the trade, not a
    consequence of it.
    """
    was, now = _starters(before, settings), _starters(after, settings)
    out = []
    for pid, row in now.items():
        if pid in was or pid in incoming:
            continue
        out.append({
            "player_id": pid,
            "player_name": row.get("player_name"),
            "position": row.get("position"),
            "slot": row.get("slot") or row.get("position"),
            "points": round(float(row.get("projected_points") or 0.0), 1),
        })
    return sorted(out, key=lambda r: -r["points"])


def reasons(
    before: pl.DataFrame,
    after: pl.DataFrame,
    settings: LeagueSettings,
    verdict: dict,
) -> dict[str, list[dict]]:
    """Pros and cons, each one traceable to a number in the verdict.

    Each entry is {stat, text}. The number travels SEPARATELY from the sentence
    so the interface can set it in its own column, in tabular figures, where
    the eye can run down it. Buried mid-sentence, twelve reasons read as twelve
    paragraphs and nobody reads the twelfth.
    """
    pros: list[dict] = []
    cons: list[dict] = []

    def pro(stat, text): pros.append({"stat": stat, "text": text})
    def con(stat, text): cons.append({"stat": stat, "text": text})

    give = verdict.get("give") or []
    get = verdict.get("get") or []
    dropped = verdict.get("dropped") or []

    # --- A SLOT WITH NOBODY IN IT COMES FIRST -----------------------------
    # Trading your only quarterback for a running back reads as "you lose this
    # by 113" and looks like a broken model until you notice the QB slot is
    # empty every week for the rest of the season. That is not a supporting
    # detail, it is the entire trade, and it was the fourth con on the list.
    empties = [s for s in _unfilled(after, settings)
               if s not in _unfilled(before, settings)]
    streamed = {f["slot"]: f for f in (verdict.get("streamed") or [])}
    for slot in empties:
        word = POS_WORD.get(slot, slot)
        lost = max((float(p.get("projected_points") or 0.0) for p in give
                    if p.get("position") == slot), default=0.0)
        fill = streamed.get(slot)
        if fill:
            # HOW BAD DEPENDS ENTIRELY ON THE POSITION, and the wire says which
            # rather than a rule about streaming. The gap between the man you
            # send and the best one nobody owns IS the cost of emptying the
            # slot -- small at quarterback, brutal at running back.
            gap = round(lost - fill["points"])
            ease = ("and that is close enough that the slot is not really the "
                    "problem" if gap <= MATERIAL * 2 else
                    "which is most of what this trade costs you")
            con(f"-{gap}" if gap > 0 else "0",
                f"No {word} left, so you would be starting {fill['player_name']} "
                f"off waivers at {round(fill['points'])} against "
                f"{round(lost)} — {ease}. Pick him up the moment this goes "
                f"through.")
        else:
            con(f"-{round(lost)}" if lost else "empty",
                f"You would have no {word} at all and nobody on the wire to "
                f"fill the slot, so it scores ZERO every week.")

    # --- what happened to the lineup ---------------------------------------
    # Slot by slot used to be four separate reasons here. It is one cascade and
    # it is now drawn as a before/after card by `lineup_moves`; repeating it as
    # a list of independent gains made a correct answer look broken. What stays
    # is the NET, which is the part a list of slots never actually said.
    b = _slot_strength(before, settings)
    a = _slot_strength(after, settings)
    net = sum(a.get(s, 0.0) for s in set(b) | set(a)) \
        - sum(b.get(s, 0.0) for s in set(b) | set(a))
    moved = sum(1 for s in set(b) | set(a)
                if abs(a.get(s, 0.0) - b.get(s, 0.0)) >= MATERIAL)
    if moved:
        word = "slot" if moved == 1 else "slots"
        if net > 0:
            pro(f"+{round(net)}", f"Your lineup rearranges: {moved} {word} move "
                f"and the starting eleven is {round(net)} points better on "
                f"projection.")
        else:
            con(f"{round(net)}", f"Your lineup rearranges: {moved} {word} move "
                f"and the starting eleven is {abs(round(net))} points worse on "
                f"projection.")

    # --- who steps up, which is what the deal really costs ----------------
    stepped = promotions(before, after, settings,
                         {p.get("player_id") for p in get})
    for p in stepped[:2]:
        # Measured against the WEAKEST man you send at that position, because
        # that is the slot he actually inherits -- the others move up ahead of
        # him. Against the best one the number was bigger and disagreed with
        # the slot line printed directly above it.
        gone = sorted((q for q in give if q.get("position") == p["position"]),
                      key=lambda q: float(q.get("projected_points") or 0.0))
        if gone:
            gap = round(float(gone[0].get("projected_points") or 0.0)
                        - p["points"])
            if gap >= MATERIAL:
                con(f"-{gap}", f"{p['player_name']} takes the {p['slot']} slot "
                    f"and he is {gap} points behind {gone[0]['player_name']}. "
                    f"That gap is the real price, not the man you send.")
            else:
                pro(f"{round(p['points'])}", f"{p['player_name']} steps into "
                    f"{p['slot']} and barely loses you anything — the depth "
                    f"was already there.")
        else:
            pro(f"{round(p['points'])}", f"{p['player_name']} moves into your "
                f"lineup at {p['slot']}, so a bench spot starts earning.")

    # --- the shape of the season, not just its middle ---------------------
    df, dc = verdict.get("delta_floor", 0.0), verdict.get("delta_ceiling", 0.0)
    if df >= MATERIAL:
        pro(f"+{round(df)}", "Higher floor — fewer ways the season goes wrong.")
    elif df <= -MATERIAL:
        con(f"-{abs(round(df))}", "Lower floor. The team gets more fragile, "
                                     "not just different.")
    if dc >= MATERIAL and df < MATERIAL:
        pro(f"+{round(dc)}", "Higher ceiling. The upside case improves even "
                              "though the floor does not.")
    if dc <= -MATERIAL and df > 0:
        con(f"-{abs(round(dc))}", "Ceiling traded for a safer floor. Right if "
                                    "you are chasing a bye, wrong if you need points.")

    # --- roster spots, which is the whole reason this engine exists -------
    if dropped:
        names = ", ".join(p["player_name"] for p in dropped)
        con(f"-{len(dropped)}", f"Over the roster limit, so it also costs you {names}.")
    gap = verdict.get("opportunity_gap", 0.0)
    if gap >= 20:
        con(f"{round(gap)}", "Raw value flatters this by that much. The gap is "
                              "bench players you cannot start.")
    elif gap <= -20:
        # WHY raw value understates it depends on whether the men leaving were
        # actually starting. The first version asserted they were not, and said
        # so about McCaffrey -- a checkable claim, so check it.
        started = {r.get("player_id") for r in
                   (optimal_lineup(before, settings)[0].iter_rows(named=True)
                    if before.height else [])}
        gave_starters = [p for p in give if p.get("player_id") in started]
        if gave_starters:
            pro(f"{abs(round(gap))}", "Raw value overstates the loss by that "
                "much. It prices everyone at full freight; your lineup only "
                "feels the slots that moved.")
        else:
            pro(f"{abs(round(gap))}", "Raw value undersells this. The men you "
                "give up were not in your lineup anyway.")

    # --- depth left behind ------------------------------------------------
    for p in give:
        pos = p.get("position")
        if not pos or pos not in settings.lineup:
            continue
        left = _depth_after(after, pos, settings)
        word = POS_WORD.get(pos, pos)
        if left <= 0:
            con("0 spare", f"No {word} left behind your starters. One injury "
                              f"and you are starting a waiver pickup.")
        elif left == 1:
            con("1 spare", f"Only one {word} behind your starters.")

    # --- consolidation, the good version of an uneven deal ----------------
    if len(get) < len(give) and verdict.get("delta_median", 0) > 0:
        pro(f"+{len(give) - len(get)} spot", f"{len(give)} players become "
            f"{len(get)} better ones, and the spare spots are yours.")
    if len(get) > len(give):
        best = max((p.get("projected_points") or 0) for p in get) if get else 0
        worst = min((p.get("projected_points") or 0) for p in get) if get else 0
        if best - worst > 60:
            con("uneven", "One of the men coming back is the trade; the rest "
                            "are filler you still have to roster.")

    # --- availability -----------------------------------------------------
    for p in get:
        g = p.get("expected_games")
        if g is not None and g < 13:
            con(f"{round(float(g), 1)} gm", f"{p['player_name']} is fragile. "
                f"You buy the risk along with the player.")
    for p in give:
        g = p.get("expected_games")
        if g is not None and g < 13:
            pro(f"{round(float(g), 1)} gm", f"You sell {p['player_name']}'s "
                f"availability risk.")

    if not pros:
        pro(None, "Nothing here improves your starting lineup.")
    if not cons:
        con(None, "No material downside. This is close to free.")
    return {"pros": pros[:6], "cons": cons[:6], "empties": empties}


def headline(verdict: dict) -> tuple[str, str]:
    """(call, sentence). The call is one of win / fair / loss.

    Every figure in the sentence is one the verdict already carries. Nothing is
    rounded up into a bigger claim, and the overlap is quoted alongside the
    gain precisely when the gain is the more flattering of the two.
    """
    d = verdict.get("delta_median", 0.0)
    p = verdict.get("win_probability", 0.5)
    pct = verdict.get("pct_change", 0.0)
    wk = verdict.get("per_week", 0.0)
    ov = (verdict.get("overlap") or {}).get("overlap")
    priced = verdict.get("roster_priced", True)
    where = "starting lineup" if priced else "the side you receive"

    size = f"{abs(round(d))} points ({abs(pct):.1f}% of your season, " \
           f"{abs(wk):.1f} a week)"
    same = (f" The two seasons still overlap {round(ov * 100)}% of the time, "
            f"so most years you would not feel it.") if ov is not None and ov > 0.8 else ""

    empty = verdict.get("empties") or []
    fills = {f["slot"]: f for f in (verdict.get("streamed") or [])}
    if empty:
        bits = []
        for slot in empty:
            word = POS_WORD.get(slot, slot)
            f = fills.get(slot)
            bits.append(f"you would be starting {f['player_name']} at {word}, "
                        f"off waivers, for {round(f['points'])}" if f
                        else f"you would have no {word} at all and nothing on "
                             f"the wire to cover it")
        gone = " And " + "; ".join(bits) + "."
    else:
        gone = ""

    if d >= 15 and p >= 0.58:
        return "win", (f"You win this trade. It adds {size} to your {where}, "
                       f"and comes out ahead in {round(p * 100)}% of simulated "
                       f"seasons.{gone}{same}")
    if d <= -15 or p < 0.42:
        return "loss", (f"You lose this trade. It costs your {where} {size}, "
                        f"and only comes out ahead in {round(p * 100)}% of "
                        f"seasons.{gone}{same}")
    return "fair", (f"This is about fair — {round(d):+d} points, "
                    f"{pct:+.1f}% of your season, {wk:+.1f} a week."
                    f"{gone}{_tiebreak(verdict)}{same}")


# How much bigger the tail move has to be than the middle before it decides a
# level trade. Two to one: below that the two numbers are saying the same thing
# with different rounding. The move also has to clear MATERIAL on its own --
# the same bar every other reason on this page has to clear, rather than a
# second threshold invented for this one sentence.
TAIL_DECIDES = 2.0


def _tiebreak(verdict: dict) -> str:
    """What settles a level trade, when the middle of it is level.

    A FAIR CALL IS THE ONE THAT NEEDS THIS MOST. The median is the middle of
    the distribution and says nothing about its shape, so two players with the
    same projection and completely different downside come back as a coin flip
    -- which is precisely the rookie-for-veteran case, where the entire
    measured difference lives in the left tail (`models/rookie_risk`: same
    total spread, 1.8x the bust rate at the top of the board).

    So when the tails have moved and the middle has not, the tails decide, and
    the sentence says which one and by how much rather than leaving you to
    read it off a chart.
    """
    d = verdict.get("delta_median", 0.0)
    fl = verdict.get("delta_floor", 0.0)
    ce = verdict.get("delta_ceiling", 0.0)
    p = verdict.get("win_probability", 0.5)
    edge = max(MATERIAL, abs(d) * TAIL_DECIDES)

    safer = fl >= edge
    riskier = fl <= -edge
    if safer and ce < 0:
        return (f" The middle is level, so the shape decides it: floor "
                f"{round(fl):+d}, ceiling {round(ce):+d}. You are buying the "
                f"safer season and selling the upside.")
    if riskier and ce > 0:
        return (f" The middle is level, so the shape decides it: floor "
                f"{round(fl):+d}, ceiling {round(ce):+d}. You are selling the "
                f"safer season and buying the swing — right only if you need "
                f"the ceiling.")
    if safer:
        return (f" The middle is level and the floor is {round(fl):+d}, so it "
                f"is the same season with less that can go wrong.")
    if riskier:
        return (f" The middle is level and the floor is {round(fl):+d}, so you "
                f"are taking on the downside for nothing in the middle.")
    return (f" It lands better in {round(p * 100)}% of seasons, near enough a "
            f"coin flip that the tiebreaker is what you need rather than what "
            f"it is worth.")
