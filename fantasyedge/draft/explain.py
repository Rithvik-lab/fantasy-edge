"""Why this player, in a sentence, from the numbers that ranked him.

The alternative was an LLM reading the board and writing prose about it. This
is better for the one reason that matters here: every clause below is a
rendering of a number the engine actually computed, so the explanation cannot
drift from the recommendation. If the text says a player will not last, it is
because `p_survive` said so, not because a sentence sounded good.

It is also free and instant, which on the clock is the whole point.
"""

from __future__ import annotations

import polars as pl

# Survival bands. Below GONE he is very unlikely to reach your next pick;
# above WAIT he probably will, which is the reach the pair value prices.
GONE = 0.25
WAIT = 0.55


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def urgency_note(p_survive: float | None, picks_ahead: int | None) -> str:
    if p_survive is None or picks_ahead is None:
        return ""
    if picks_ahead <= 0:
        return "last pick of the draft"
    if p_survive < GONE:
        return f"almost certainly gone in {picks_ahead} picks ({_pct(p_survive)} to last)"
    if p_survive > WAIT:
        return f"likely still there in {picks_ahead} picks ({_pct(p_survive)} to last)"
    return f"a coin flip to last {picks_ahead} picks ({_pct(p_survive)})"


def reasons(row: dict, context: dict) -> list[str]:
    """Ranked list of the specific reasons this player is on the shortlist.

    `context` carries board-level facts: picks_until_next, needs, the
    positional dropoff table, and the target volatility for this pick.
    """
    out: list[str] = []
    pos = row.get("position") or "?"
    vor = row.get("vor")
    gap = context.get("picks_until_next")

    # 1. Value, stated against replacement rather than in the abstract.
    if vor is not None:
        if vor > 0:
            out.append(
                f"{vor:.0f} points above what you could start for free at {pos}")
        else:
            out.append(
                f"{abs(vor):.0f} points BELOW replacement at {pos} -- "
                "depth, not a starter")

    # 2. Whether the pick has to happen now. This is the reach test.
    note = urgency_note(row.get("p_survive"), gap)
    if note:
        out.append(note)

    # 3. Positional scarcity, only when it is actually saying something.
    drop = context.get("dropoff", {}).get(pos)
    if drop is not None and drop > 8:
        out.append(f"{pos} falls off {drop:.0f} points if you wait a turn")

    # 4. Roster fit.
    needs = context.get("needs") or {}
    if needs.get(pos, 0) > 0:
        n = needs[pos]
        out.append(f"fills a starting {pos} slot ({n} still open)")
    elif needs.get("FLEX", 0) > 0 and pos in ("RB", "WR", "TE"):
        out.append("fills your FLEX")
    elif row.get("need_mult", 1.0) < 1.0:
        out.append(f"{pos} is already covered, so this is value over need")

    # 5. The shape of the season, not just its middle.
    floor, ceil = row.get("floor"), row.get("ceiling")
    if floor is not None and ceil is not None:
        out.append(f"season range {floor:.0f} to {ceil:.0f} points")

    # 6. Risk, relative to what this pick should be looking for.
    vol, target = row.get("vol_pct"), context.get("target_vol")
    if vol is not None and target is not None:
        if vol < target - 0.20:
            out.append("steadier than this pick needs -- a floor play")
        elif vol > target + 0.20:
            out.append("swingier than this pick wants -- upside at a cost")

    # 7. Where the points come from. In full PPR this is the floor mechanism.
    rec, td = row.get("rec_share"), row.get("td_share")
    if rec is not None and rec > 0.35:
        out.append(f"{_pct(rec)} of his points come from catches, the most "
                   "repeatable scoring there is")
    elif td is not None and td > 0.33:
        out.append(f"{_pct(td)} of his points come from touchdowns, which do "
                   "not carry over season to season")

    # 8. Rookie, with the measured reason rather than a vibe.
    if row.get("rookie"):
        out.append("rookie -- at the top of the board they return less than "
                   "projected and bust at nearly twice the veteran rate")

    return out


def headline(row: dict, context: dict) -> str:
    """One line, the single most decision-relevant fact about this pick."""
    pos = row.get("position") or "?"
    p = row.get("p_survive")
    gap = context.get("picks_until_next")
    vor = row.get("vor") or 0.0

    if p is not None and gap and p < GONE:
        return f"Best {pos} left and he will not reach {gap} picks from now."
    if p is not None and gap and p > WAIT:
        return (f"Strong value, but {_pct(p)} says you could still have him "
                f"at your next pick.")
    drop = context.get("dropoff", {}).get(pos)
    if drop is not None and drop > 12:
        return f"{pos} is about to fall off a cliff -- {drop:.0f} points by your next turn."
    if vor > 0:
        return f"The most value on the board relative to {pos} replacement."
    return "Best remaining depth for a roster with starters filled."


def annotate(rec: pl.DataFrame, context: dict) -> list[dict]:
    """Attach headline and reasons to each recommendation row."""
    out = []
    for r in rec.iter_rows(named=True):
        d = dict(r)
        d["headline"] = headline(r, context)
        d["reasons"] = reasons(r, context)
        out.append(d)
    return out


def compare(top: list[dict]) -> str:
    """Why number one and not number two -- the question actually being asked."""
    if len(top) < 2:
        return ""
    a, b = top[0], top[1]
    da = (a.get("score") or 0) - (b.get("score") or 0)
    if da < 2:
        return (f"{a['player_name']} and {b['player_name']} are effectively "
                f"tied; take the one whose position you would rather not chase.")

    pa, pb = a.get("p_survive"), b.get("p_survive")
    if pa is not None and pb is not None and pb > pa + 0.15:
        return (f"{a['player_name']} first because {b['player_name']} is "
                f"{_pct(pb)} to still be there at your next pick and "
                f"{a['player_name']} is {_pct(pa)}.")
    va, vb = a.get("vor") or 0, b.get("vor") or 0
    if va > vb:
        return (f"{a['player_name']} first on value -- {va - vb:.0f} more "
                f"points over replacement than {b['player_name']}.")
    return (f"{a['player_name']} first: {b['player_name']} has more raw value, "
            f"but taking him costs more of your next pick.")
