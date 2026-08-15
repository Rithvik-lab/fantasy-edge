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

    # --- what happened to the lineup, slot by slot ------------------------
    b = _slot_strength(before, settings)
    a = _slot_strength(after, settings)
    moves = sorted(
        ((slot, a.get(slot, 0.0) - b.get(slot, 0.0))
         for slot in set(b) | set(a)),
        key=lambda kv: -abs(kv[1]),
    )
    for slot, d in moves:
        if abs(d) < MATERIAL:
            continue
        if d > 0:
            pro(f"+{round(d)}", f"Your {slot} slot gets better.")
        else:
            con(f"-{abs(round(d))}", f"Your {slot} slot gets worse.")

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
    return {"pros": pros[:6], "cons": cons[:6]}


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

    if d >= 15 and p >= 0.58:
        return "win", (f"You win this trade. It adds {size} to your {where}, "
                       f"and comes out ahead in {round(p * 100)}% of simulated "
                       f"seasons.{same}")
    if d <= -15 or p < 0.42:
        return "loss", (f"You lose this trade. It costs your {where} {size}, "
                        f"and only comes out ahead in {round(p * 100)}% of "
                        f"seasons.{same}")
    return "fair", (f"This is about fair — {round(d):+d} points, "
                    f"{pct:+.1f}% of your season, {wk:+.1f} a week."
                    f"{_tiebreak(verdict)}{same}")


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
