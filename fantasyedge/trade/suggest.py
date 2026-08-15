"""Find trades worth offering.

THE SEARCH IS THE PROBLEM

Eleven opponents, sixteen men each, sixteen of ours. Every one-for-one,
two-for-one and one-for-two is about 45,000 candidate deals, and a full
verdict is two season simulations. Simulating all of them would take an hour
to answer a question you asked between games.

So it runs in three passes, cheapest first, and each pass only hands on what
survives:

  1. PRUNE     on perceived value. A deal the other manager reads as a
               fleecing never gets offered, so it never needs pricing. This
               alone removes most of the space, for free, using arithmetic we
               need anyway.
  2. RANK      on a deterministic lineup delta -- best legal lineup, projected
               points, no simulation. Directionally right and roughly a
               thousand times cheaper than the real thing.
  3. VERDICT   full paired simulation on the survivors only.

WHAT MAKES A TRADE OFFERABLE

Both of these, and neither is optional:

  it wins for us      on the simulated lineup, which is the only scale that
                      prices roster spots honestly
  it reads as a win   for them on THEIR scale -- draft capital plus an
                      overweighted read of recent form

A deal that fails the second is not a trade, it is a wish. The gap between
the two scales is the whole product.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import polars as pl

from fantasyedge.draft.session import optimal_lineup
from fantasyedge.league import LeagueSettings
from fantasyedge.trade import market
from fantasyedge.trade.ask import Ask
from fantasyedge.trade.evaluate import evaluate

# How generous the offer has to look from across the table. Zero means "they
# break even"; a real offer needs to be visibly good or it gets ignored.
THEIR_MIN_GAIN = 5.0

# The one dial the user actually turns. Every stance still requires that WE
# gain -- what changes is how much the other side has to gain as well, which
# is the same thing as how lopsided we are willing for the offer to look.
#
# Fleece is not "take more", it is "give them the least that still gets a yes".
# It produces the biggest edge and the most rejections; conservative produces
# the opposite. Neither one changes what the deal is actually worth to us --
# only which deals get shown.
STANCE: dict[str, float] = {
    "conservative": 28.0,
    "fair": 12.0,
    "fleece": 3.0,
}
# Ours, on the honest scale. Below this it is not worth the message.
OUR_MIN_GAIN = 6.0

# WHAT MAKES THEM SAY YES IS BOTH THINGS AT ONCE.
#
# A manager with three good receivers does not want a fourth, and one starting
# a waiver-wire back will overpay for any back at all. The capital number knows
# none of that -- it prices a player the same wherever he lands. Their LINEUP
# knows it exactly: a fourth receiver dropped on a deep team moves their best
# legal eleven by nothing, and a starter dropped into a hole moves it a lot.
#
# So acceptance is the sum of the two, both in points: what they think they
# gained, plus what it actually fixes. A deal handing them a name they love
# needs to fix nothing; a deal that plugs their weakest slot does not have to
# flatter them as much. Requiring each SEPARATELY was the first version and it
# was far too strict -- of 20,808 candidate deals against one opponent, 448
# improved our lineup and 0 cleared a separate six-point bar on theirs. Ten of
# eleven managers became untradeable with, which is not a league anyone has
# ever played in.
#
# Weight one, because both are season points measured the same way. There is no
# tuned coefficient here and there should not be one without trade data to fit
# it against.
def acceptance(capital: float, lineup: float) -> float:
    return capital + max(lineup, 0.0)
# How many survive the cheap pass and get simulated properly.
SHORTLIST = 24
# Fewer sims than a single reported verdict: this is ranking, not the answer.
SCAN_SIMS = 1500


@dataclass
class Offer:
    team_id: int
    team_name: str
    give: list[dict]
    get: list[dict]
    our_gain: float          # simulated starting-lineup points
    their_gain: float        # on their scale, which is what gets it accepted
    win_probability: float
    naive_delta: float
    # What it does to THEIR starting lineup. The capital number says whether
    # they like the names; this says whether the deal fixes anything for them,
    # and it is the half that understands a fourth receiver is worth less to a
    # team that already starts three.
    their_lineup: float = 0.0
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "team_id": self.team_id,
            "team_name": self.team_name,
            "give": self.give,
            "get": self.get,
            "our_gain": round(self.our_gain, 1),
            "their_gain": round(self.their_gain, 1),
            "their_lineup": round(self.their_lineup, 1),
            "win_probability": round(self.win_probability, 3),
            "naive_delta": round(self.naive_delta, 1),
            "note": self.note,
        }


def _lineup_points(roster: pl.DataFrame, settings: LeagueSettings) -> float:
    """Deterministic best-lineup total, via the shared slotting rules."""
    if not roster.height:
        return 0.0
    starters, _ = optimal_lineup(roster, settings)
    if not starters.height:
        return 0.0
    return float(starters["projected_points"].fill_null(0.0).sum())


def _fast_lineup(players: list[tuple[str, float]], settings: LeagueSettings) -> float:
    """The same answer as `_lineup_points`, in microseconds instead of millis.

    WHY THIS EXISTS AT ALL

    `optimal_lineup` costs ~2.6ms, which is nothing until you call it 400,000
    times. The scan evaluates 18,000 candidate deals per opponent and needs two
    lineups for each, so the first version of this search took EIGHTEEN MINUTES
    to answer a question you ask between games.

    None of that was the algorithm. It was polars per-call overhead on frames
    of sixteen rows -- the wrong tool at this size, where the whole roster fits
    in a python list and the fill is a sort and a couple of slices.

    It has to agree with `optimal_lineup` exactly or the shortlist is ranked on
    one rule and reported on another; `tests/test_suggest.py` checks that.
    """
    by_pos: dict[str, list[float]] = {}
    for pos, pts in players:
        by_pos.setdefault(pos, []).append(pts)
    for v in by_pos.values():
        v.sort(reverse=True)

    total = 0.0
    leftover: list[float] = []
    for slot, count in settings.lineup.items():
        if slot in ("FLEX", "SUPERFLEX"):
            continue
        pool = by_pos.get(slot)
        if not pool:
            continue
        total += sum(pool[:count])
        if slot in settings.flex_eligible:
            leftover.extend(pool[count:])

    flex = settings.lineup.get("FLEX", 0)
    if flex and leftover:
        leftover.sort(reverse=True)
        total += sum(leftover[:flex])
    return total


def _as_rows(df: pl.DataFrame) -> dict[str, tuple[str, float]]:
    """player_id -> (position, projected points). Read once, reused everywhere."""
    return {
        r["player_id"]: (r["position"], float(r.get("projected_points") or 0.0))
        for r in df.select(["player_id", "position", "projected_points"])
                   .iter_rows(named=True)
    }


def _swap(roster: pl.DataFrame, out_ids: list[str], incoming: pl.DataFrame) -> pl.DataFrame:
    kept = roster.filter(~pl.col("player_id").is_in(out_ids))
    if not incoming.height:
        return kept
    cols = [c for c in kept.columns if c in incoming.columns]
    return pl.concat([kept.select(cols), incoming.select(cols)], how="vertical")


def _packages(df: pl.DataFrame, max_size: int,
              exclude: set[str] | None = None) -> list[tuple[str, ...]]:
    ids = [i for i in df["player_id"].to_list()
           if not exclude or i not in exclude]
    out: list[tuple[str, ...]] = [(i,) for i in ids]
    if max_size >= 2:
        out += list(combinations(ids, 2))
    return out


def key(give, get) -> str:
    """A stable name for a deal, so re-roll can avoid showing it twice."""
    return "|".join(sorted(give)) + ">" + "|".join(sorted(get))


def for_team(
    mine: pl.DataFrame,
    theirs: pl.DataFrame,
    board: pl.DataFrame,
    settings: LeagueSettings,
    team_id: int,
    team_name: str,
    max_out: int = 2,
    max_in: int = 2,
    top: int = 3,
    ask: "Ask | None" = None,
    seen: set[str] | None = None,
) -> list[Offer]:
    """Best offers to send one opponent.

    `ask` is what you said was wrong with the last one -- men to hold, a
    position you want back, a package size. It narrows the search rather than
    re-ranking its output: a constraint applied afterwards throws away the work
    that would have satisfied it.
    """
    if not mine.height or not theirs.height:
        return []
    ask = ask or Ask()
    seen = seen or set()
    max_out = min(max_out, ask.max_out)
    max_in = min(max_in, ask.max_in)

    pv = dict(zip(board["player_id"].to_list(),
                  board["perceived_value"].fill_null(0.0).to_list()))

    # Everything the cheap pass needs, pulled out of polars ONCE. Inside the
    # loop this is plain python and stays that way.
    mine_rows = _as_rows(mine)
    their_rows = _as_rows(theirs)
    limit = settings.roster_size

    base_ours = _fast_lineup(list(mine_rows.values()), settings)
    base_theirs = _fast_lineup(list(their_rows.values()), settings)

    pos = dict(zip(board["player_id"].to_list(), board["position"].to_list()))
    give_sets = _packages(mine, max_out, exclude=set(ask.keep))
    get_sets = _packages(theirs, max_in)
    if ask.must_get:
        want = set(ask.must_get)
        get_sets = [k for k in get_sets if want & set(k)]
    if ask.want:
        get_sets = [k for k in get_sets
                    if {pos.get(i) for i in k} & set(ask.want)]
    if not give_sets or not get_sets:
        return []
    floor = OUR_MIN_GAIN * (2.0 if ask.richer else 1.0)

    # --- pass 1 + 2: prune on their scale, rank on ours ------------------
    scored = []
    for g in give_sets:
        g_pv = sum(pv.get(i, 0.0) for i in g)
        kept_ours = [v for pid, v in mine_rows.items() if pid not in g]
        out_rows = [mine_rows[i] for i in g if i in mine_rows]

        for k in get_sets:
            # What they gain, as they see it: they receive our package and
            # give up theirs. Checked first because it is one subtraction and
            # it eliminates most of the space -- but only at zero, because a
            # deal that fixes their lineup can be worth taking at a small
            # capital loss and the lineup is not computed yet here.
            capital = g_pv - sum(pv.get(i, 0.0) for i in k)
            if capital < 0:
                continue

            in_rows = [their_rows[i] for i in k if i in their_rows]
            after_ours = kept_ours + in_rows
            if len(after_ours) > limit:
                after_ours = sorted(after_ours, key=lambda r: -r[1])[:limit]
            cheap = _fast_lineup(after_ours, settings) - base_ours
            if cheap <= 0:
                continue

            # Their roster has to actually work afterwards, or they will see
            # the hole even if the arithmetic flatters them -- and it has to
            # work BETTER by enough to be worth their trouble, which is where
            # positional scarcity enters on their side.
            after_theirs = [v for pid, v in their_rows.items()
                            if pid not in k] + out_rows
            theirs_lineup = _fast_lineup(after_theirs, settings) - base_theirs
            # Never propose something that makes their team worse -- they can
            # see that too -- and clear the bar on the two together.
            if theirs_lineup < 0:
                continue
            if acceptance(capital, theirs_lineup) < THEIR_MIN_GAIN:
                continue

            scored.append((cheap, capital, g, k, theirs_lineup))

    if not scored:
        return []

    scored.sort(key=lambda r: -r[0])
    offers: list[Offer] = []

    # --- pass 3: the real verdict, survivors only ------------------------
    for cheap, their_gain, g, k, theirs_lineup in scored[:SHORTLIST]:
        if key(g, k) in seen:
            # Re-roll means SHOW ME ANOTHER ONE. Returning the same deal with
            # the same numbers reads as a broken button, which is what it was.
            continue
        v = evaluate(mine, list(g), list(k), settings, board, n_sims=SCAN_SIMS)
        if v.delta_median < floor:
            continue
        offers.append(Offer(
            team_id=team_id,
            team_name=team_name,
            give=v.give,
            get=v.get,
            our_gain=v.delta_median,
            their_gain=their_gain,
            their_lineup=theirs_lineup,
            win_probability=v.win_probability,
            naive_delta=v.naive_value_delta,
            note=v.note,
        ))

    offers.sort(key=lambda o: -o.our_gain)
    return offers[:top]


def counter(
    mine: pl.DataFrame,
    theirs: pl.DataFrame,
    board: pl.DataFrame,
    settings: LeagueSettings,
    give: list[str],
    get: list[str],
    stance: str = "fair",
    observed: pl.DataFrame | None = None,
    through_week: int = 0,
    top: int = 3,
    ask: "Ask | None" = None,
    seen: set[str] | None = None,
) -> list[Offer]:
    """Given a deal on the table, find nearby deals that are better.

    NOT a rule. The obvious version of this -- "ask them for one more small
    player" -- is usually wrong, because a small player is exactly the thing
    that costs you a roster spot and starts for nobody. Sometimes the fix is
    asking for one better piece instead of two; sometimes it is giving up MORE
    so their side works; sometimes it is a straight swap of who you send.

    So it searches rather than prescribes: every single-player addition,
    removal and substitution on either side, scored the same way as the main
    scan and simulated properly at the end. Whatever comes back is what
    actually helps, which is what was asked for.
    """
    b = market.perceived(board, observed, through_week)
    pv = dict(zip(b["player_id"].to_list(),
                  b["perceived_value"].fill_null(0.0).to_list()))
    ask = ask or Ask()
    seen = seen or set()
    threshold = STANCE.get(stance, THEIR_MIN_GAIN)
    if ask.harder:
        threshold = max(threshold, STANCE["conservative"])

    mine_rows = _as_rows(mine)
    their_rows = _as_rows(theirs)
    my_ids = [p for p in mine_rows if p not in give and p not in set(ask.keep)]
    their_ids = [p for p in their_rows if p not in get]

    base_ours = _fast_lineup(list(mine_rows.values()), settings)
    base_theirs = _fast_lineup(list(their_rows.values()), settings)
    limit = settings.roster_size

    # Every deal one move away from the one on the table.
    variants: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    g0, k0 = tuple(give), tuple(get)
    for pid in my_ids:                       # offer one more of ours
        variants.add((tuple(sorted(g0 + (pid,))), k0))
    for pid in their_ids:                    # ask for one more of theirs
        variants.add((g0, tuple(sorted(k0 + (pid,)))))
    for pid in give:                         # stop offering one of ours
        variants.add((tuple(x for x in g0 if x != pid), k0))
    for pid in get:                          # stop asking for one of theirs
        variants.add((g0, tuple(x for x in k0 if x != pid)))
    for out in give:                         # swap who we send
        for pid in my_ids:
            variants.add((tuple(sorted([x for x in g0 if x != out] + [pid])), k0))
    for out in get:                          # swap who we ask for
        for pid in their_ids:
            variants.add((g0, tuple(sorted([x for x in k0 if x != out] + [pid]))))
    variants.discard((g0, k0))

    scored = []
    for g, k in variants:
        if not g and not k:
            continue
        their_gain = (sum(pv.get(i, 0.0) for i in g)
                      - sum(pv.get(i, 0.0) for i in k))
        if their_gain < 0:
            continue
        kept = [v for pid, v in mine_rows.items() if pid not in g]
        incoming = [their_rows[i] for i in k if i in their_rows]
        after = kept + incoming
        if len(after) > limit:
            after = sorted(after, key=lambda r: -r[1])[:limit]
        cheap = _fast_lineup(after, settings) - base_ours
        if cheap <= 0:
            continue
        after_theirs = ([v for pid, v in their_rows.items() if pid not in k]
                        + [mine_rows[i] for i in g if i in mine_rows])
        theirs_lineup = _fast_lineup(after_theirs, settings) - base_theirs
        if theirs_lineup < 0:
            continue
        if acceptance(their_gain, theirs_lineup) < threshold:
            continue
        scored.append((cheap, their_gain, g, k, theirs_lineup))

    if not scored:
        return []
    scored.sort(key=lambda r: -r[0])

    out_offers: list[Offer] = []
    floor = OUR_MIN_GAIN * (2.0 if ask.richer else 1.0)
    for _, their_gain, g, k, theirs_lineup in scored[:SHORTLIST]:
        if key(g, k) in seen:
            continue
        v = evaluate(mine, list(g), list(k), settings, board, n_sims=SCAN_SIMS)
        if v.delta_median < floor:
            continue
        out_offers.append(Offer(
            team_id=-1, team_name="counter", give=v.give, get=v.get,
            our_gain=v.delta_median, their_gain=their_gain,
            their_lineup=theirs_lineup,
            win_probability=v.win_probability, naive_delta=v.naive_value_delta,
            note=v.note))
    out_offers.sort(key=lambda o: -o.our_gain)
    return out_offers[:top]


def across_league(
    mine: pl.DataFrame,
    rosters: dict[int, pl.DataFrame],
    board: pl.DataFrame,
    settings: LeagueSettings,
    names: dict[int, str] | None = None,
    observed: pl.DataFrame | None = None,
    through_week: int = 0,
    per_team: int = 2,
    top: int = 10,
    ask: "Ask | None" = None,
    seen: set[str] | None = None,
) -> list[Offer]:
    """Scan every opponent. `rosters` excludes yours."""
    b = market.perceived(board, observed, through_week)
    names = names or {}

    out: list[Offer] = []
    for tid, roster in rosters.items():
        out += for_team(mine, roster, b, settings, tid,
                        names.get(tid, f"Team {tid}"), top=per_team,
                        ask=ask, seen=seen)

    out.sort(key=lambda o: -o.our_gain)
    return out[:top]
