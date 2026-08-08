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
) -> dict[str, list[str]]:
    """Pros and cons, each one traceable to a number in the verdict."""
    pros: list[str] = []
    cons: list[str] = []

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
            pros.append(f"Your {slot} slot gets {round(d)} points better.")
        else:
            cons.append(f"Your {slot} slot drops {abs(round(d))} points.")

    # --- the shape of the season, not just its middle ---------------------
    df, dc = verdict.get("delta_floor", 0.0), verdict.get("delta_ceiling", 0.0)
    if df >= MATERIAL:
        pros.append(f"Your floor rises {round(df)} — fewer ways this season "
                    f"goes wrong.")
    elif df <= -MATERIAL:
        cons.append(f"Your floor drops {abs(round(df))}. This makes the team "
                    f"more fragile, not just different.")
    if dc >= MATERIAL and df < MATERIAL:
        pros.append(f"Your ceiling rises {round(dc)}, so the upside case gets "
                    f"better even though the floor does not.")
    if dc <= -MATERIAL and df > 0:
        cons.append(f"You trade {abs(round(dc))} of ceiling for a safer floor. "
                    f"Right if you are chasing a bye, wrong if you need points.")

    # --- roster spots, which is the whole reason this engine exists -------
    if dropped:
        names = ", ".join(p["player_name"] for p in dropped)
        cons.append(f"You are over the roster limit, so this also costs you "
                    f"{names}.")
    gap = verdict.get("opportunity_gap", 0.0)
    if gap >= 20:
        cons.append(f"On raw value this looks {round(gap)} points better than "
                    f"it is. That gap is bench players you cannot start.")
    elif gap <= -20:
        # WHY raw value understates it depends on whether the men leaving were
        # actually starting. The first version asserted they were not, and said
        # so about McCaffrey -- a checkable claim, so check it.
        started = {r.get("player_id") for r in
                   (optimal_lineup(before, settings)[0].iter_rows(named=True)
                    if before.height else [])}
        gave_starters = [p for p in give if p.get("player_id") in started]
        if gave_starters:
            pros.append(
                f"Raw value overstates the loss by {abs(round(gap))}. It counts "
                f"everyone at full price; your lineup only feels the slots that "
                f"actually changed.")
        else:
            pros.append(f"Raw value undersells this by {abs(round(gap))} — the "
                        f"men you give up were not in your lineup anyway.")

    # --- depth left behind ------------------------------------------------
    for p in give:
        pos = p.get("position")
        if not pos or pos not in settings.lineup:
            continue
        left = _depth_after(after, pos, settings)
        word = POS_WORD.get(pos, pos)
        if left <= 0:
            cons.append(f"Giving up {p['player_name']} leaves you with no spare "
                        f"{word} at all — one injury and you are starting a "
                        f"waiver pickup.")
        elif left == 1:
            cons.append(f"You are down to one spare {word} behind your starters.")

    # --- consolidation, the good version of an uneven deal ----------------
    if len(get) < len(give) and verdict.get("delta_median", 0) > 0:
        pros.append(f"You turn {len(give)} players into {len(get)} better ones "
                    f"and free up {len(give) - len(get)} roster spot(s).")
    if len(get) > len(give):
        best = max((p.get("projected_points") or 0) for p in get) if get else 0
        worst = min((p.get("projected_points") or 0) for p in get) if get else 0
        if best - worst > 60:
            cons.append("The players coming back are uneven — one of them is "
                        "the trade and the rest are filler you have to roster.")

    # --- availability -----------------------------------------------------
    for p in get:
        g = p.get("expected_games")
        if g is not None and g < 13:
            cons.append(f"{p['player_name']} is only projected for "
                        f"{round(float(g), 1)} games. You are buying the risk "
                        f"as well as the player.")
    for p in give:
        g = p.get("expected_games")
        if g is not None and g < 13:
            pros.append(f"You are selling {p['player_name']}'s availability "
                        f"risk — {round(float(g), 1)} projected games.")

    if not pros:
        pros.append("Nothing here improves your starting lineup.")
    if not cons:
        cons.append("No material downside — this is close to free.")
    return {"pros": pros[:6], "cons": cons[:6]}


def headline(verdict: dict) -> tuple[str, str]:
    """(call, sentence). The call is one of win / fair / loss."""
    d = verdict.get("delta_median", 0.0)
    p = verdict.get("win_probability", 0.5)
    priced = verdict.get("roster_priced", True)
    where = "starting lineup" if priced else "two sides"

    if d >= 15 and p >= 0.58:
        return "win", (f"You win this trade. It adds {round(d)} points to your "
                       f"{where} and comes out ahead in {round(p * 100)}% of "
                       f"simulated seasons.")
    if d <= -15 or p < 0.42:
        return "loss", (f"You lose this trade. It costs your {where} "
                        f"{abs(round(d))} points and only comes out ahead in "
                        f"{round(p * 100)}% of seasons.")
    return "fair", (f"This is about fair. {round(d):+d} points either way is "
                    f"inside the noise, and it lands better in "
                    f"{round(p * 100)}% of seasons — near enough a coin flip "
                    f"that the tiebreaker is what you need, not what it is worth.")
