"""Who to add, and who to drop for him.

THE QUESTION IS NOT "WHO IS THE BEST FREE AGENT"

Every waiver tool ranks the wire by projection and stops. That list is the same
for all twelve managers in the league, which is the tell: a ranking that does
not know your roster cannot be answering a question about your roster. The
useful question has your team in it twice --

    does he crack MY lineup, and what do I lose to make room for him

-- and both halves move the answer. The best receiver on the wire is worth
nothing to a team already starting three better ones, and a middling tight end
is worth a great deal to a team starting nobody there. Same player, same week,
opposite verdicts.

WHAT A CLAIM IS WORTH

The difference between the lineup you can field with him and the lineup you can
field now, after the drop his arrival forces. That is the same measure the
trade engine uses, and it has to be: adding a man off waivers IS a trade, with
the wire as the counterparty and a roster spot as the price.

So a claim that improves nothing scores zero however good the player is, a
claim that fills an empty slot scores what the slot was costing you, and a
claim that forces you to cut somebody who starts pays for that cut.
"""

from __future__ import annotations

import polars as pl

from fantasyedge.draft.session import optimal_lineup
from fantasyedge.league import LeagueSettings

# Positions where the wire is a real strategy rather than a shrug. Kicker and
# defence stay in -- streaming them is the most repeatable edge in the format,
# and it is the one part of this that gets BETTER once games exist, because the
# weekly model in `models.kdst` prices a matchup rather than a season.
CLAIMABLE = ("QB", "RB", "WR", "TE", "K", "DST")

# Below this a claim is not worth a waiver priority. Half a point a week.
MIN_ADD = 8.0

# How many survive the cheap ranking pass and get simulated properly. The wire
# is three hundred names and a simulation is not free; this is the same shape
# as the trade search, which shortlists twenty-four out of twenty thousand.
SHORTLIST = 18


def _num(v) -> float | None:
    """Round for the wire, and let a missing number stay missing."""
    return None if v is None else round(float(v), 1)


def _lineup_points(roster: pl.DataFrame, settings: LeagueSettings) -> float:
    if not roster.height:
        return 0.0
    starters, _ = optimal_lineup(roster, settings)
    return float(starters["projected_points"].fill_null(0.0).sum()) \
        if starters.height else 0.0


def _cuts(roster: pl.DataFrame, settings: LeagueSettings, base: float,
          board: pl.DataFrame | None = None, free: pl.DataFrame | None = None,
          n_sims: int = 2000) -> list[tuple[str, str, float]]:
    """Everyone on the roster, ordered by what losing him costs the lineup.

    Not by projection. The last bench receiver is usually a smaller loss than a
    kicker however the two look side by side, because the kicker is starting
    and the receiver is not.

    AND NOT BY THE DETERMINISTIC LINEUP EITHER, which is the version this
    started as. That measure prices every bench player at exactly zero -- it
    solves one lineup, on projections, in which no starter is ever hurt -- so
    on a full roster every candidate tied at 0.0 and the cut fell to whoever
    happened to sort first. It picked a startable running back over a spare
    kicker and reported the cost as "-0.0".

    So the cut is simulated, the same way the claim it pays for is. One pass
    over the roster, once per request: eighteen simulations, about two seconds,
    and the difference between dropping the right man and dropping a random one.
    """
    if board is None:
        return sorted(
            [(r["player_id"], r["player_name"],
              base - _lineup_points(
                  roster.filter(pl.col("player_id") != r["player_id"]), settings))
             for r in roster.iter_rows(named=True)],
            key=lambda c: c[2])

    from fantasyedge.trade.evaluate import evaluate

    out = []
    for r in roster.iter_rows(named=True):
        v = evaluate(roster, [r["player_id"]], [], settings, board,
                     n_sims=n_sims, free_agents=free)
        # Losing him is a negative delta; the COST is how negative.
        out.append((r["player_id"], r["player_name"], -v.delta_mean))
    out.sort(key=lambda c: c[2])
    return out


def claims(roster: pl.DataFrame, free: pl.DataFrame, settings: LeagueSettings,
           top: int = 12, limit: int | None = None,
           board: pl.DataFrame | None = None, n_sims: int = 1500,
           floor: float | None = MIN_ADD, shortlist: int = SHORTLIST,
           cuts: list[tuple[str, str, float]] | None = None,
           pool_all: pl.DataFrame | None = None) -> list[dict]:
    """Rank the wire by what each man would do to YOUR starting lineup.

    TWO PASSES, for the same reason the trade search has three. The cheap one
    is the best legal lineup on projections, which is fast and blind: it cannot
    see that your only quarterback misses two games a year, so it scores every
    backup at exactly zero and reports that the wire has nothing. Run alone it
    said precisely that about a roster with two spare spots and no backup
    quarterback in the league's top hundred and fifty.

    So the cheap pass RANKS and does not judge, and the survivors are priced by
    the same season simulation a trade gets -- because a claim IS a trade, with
    the wire as the counterparty and a roster spot as the price.

    `limit` is the roster size; when the roster is full every claim costs a
    drop, and the drop is part of the price rather than a footnote under it.

    `free` is who to CONSIDER and `pool_all` is the wire an empty slot would be
    filled from. They are the same thing when the whole wire is being ranked
    and they are not when one position is -- searching tight ends with the
    tight-end pool as the floor tells the simulator that an empty quarterback
    slot gets filled by a tight end, and the baseline it measures against
    stops being your roster.
    """
    if not free.height:
        return []
    limit = limit or settings.roster_size
    base = _lineup_points(roster, settings)
    full = roster.height >= limit

    # Who goes if somebody has to, cheapest first. Passed in when a caller is
    # asking the same question six times over (once per position) -- the cut
    # order is a property of the roster and does not change with the pool.
    if cuts is None:
        cuts = (_cuts(roster, settings, base, board,
                      pool_all if pool_all is not None else free, n_sims)
                if full and roster.height else [])

    pool = (free.filter(pl.col("position").is_in(list(CLAIMABLE)))
                .sort("projected_points", descending=True, nulls_last=True)
                .head(120))

    drop_id = drop_name = None
    drop_cost = 0.0
    if full and cuts:
        drop_id, drop_name, drop_cost = cuts[0]

    # --- pass one: rank, do not judge ------------------------------------
    cols = [c for c in roster.columns if c in pool.columns]
    ranked = []
    for r in pool.iter_rows(named=True):
        one = pool.filter(pl.col("player_id") == r["player_id"])
        added = pl.concat([roster.select(cols), one.select(cols)], how="vertical")
        if drop_id:
            added = added.filter(pl.col("player_id") != drop_id)
        ranked.append((_lineup_points(added, settings) - base, r))
    ranked.sort(key=lambda x: -x[0])

    # --- pass two: price the survivors properly --------------------------
    from fantasyedge.trade.evaluate import evaluate

    out = []
    for cheap, r in ranked[:shortlist]:
        gain = cheap
        if board is not None:
            v = evaluate(roster, [drop_id] if drop_id else [], [r["player_id"]],
                         settings, board, n_sims=n_sims,
                         free_agents=pool_all if pool_all is not None else free)
            # THE MEAN, NOT THE MEDIAN, AND ONLY HERE. A trade changes your
            # starting lineup, so the middle season is the right summary of it.
            # A waiver claim usually does not: in the median season your
            # starters play, the new man never leaves the bench, and the median
            # is exactly 0.0 -- which it was, for every claim on the wire, at
            # every simulation count.
            #
            # A backup's entire value is the seasons where something goes
            # wrong. Summarising him with the statistic that discards tails
            # prices depth at zero all over again, which is the third time the
            # same mistake has been found in this engine.
            gain = v.delta_mean
        # `floor=None` browses rather than recommends. The positional lists
        # have to show the next man up even when he is not worth a claim today,
        # because the whole point of them is what to do when your first choice
        # is gone before your priority comes up.
        if floor is not None and gain < floor:
            continue
        out.append({
            "player_id": r["player_id"],
            "player_name": r["player_name"],
            "position": r["position"],
            "ecr": r.get("ecr"),
            "projected_points": round(float(r.get("projected_points") or 0.0), 1),
            "adds": round(gain, 1),
            "starts": round(cheap, 1),
            "drop_id": drop_id,
            "drop_name": drop_name,
            "drop_cost": round(drop_cost, 1),
            # The shape of the season behind the single number, so the card can
            # draw a range rather than assert a point.
            "floor": _num(r.get("season_p20")),
            "ceiling": _num(r.get("season_p80")),
            "games": _num(r.get("expected_games")),
            # How much of the wider ESPN world rosters him. Not a projection --
            # a fact about the market, and the only signal here that knows
            # something your league does not.
            "owned": _num(r.get("percent_owned")),
        })

    out.sort(key=lambda c: -c["adds"])
    return out[:top]


def best(groups: dict[str, list[dict]], top: int = 12,
         floor: float | None = MIN_ADD) -> list[dict]:
    """The headline list, taken FROM the positional lists rather than beside them.

    Ranking the whole wire in one pass and ranking it per position are two
    different searches, and they disagreed: the single pass shortlisted on a
    deterministic lineup, which cannot see a backup at all, so it put two
    quarterbacks at the top at +9.7 while the tight end column -- searched
    within its own position, where he had room to place -- had a man at +11.4.
    Two numbers on one screen, both computed, contradicting each other.

    So there is one search now and the headline is a view of it.
    """
    flat = [c for men in groups.values() for c in men]
    if floor is not None:
        flat = [c for c in flat if c["adds"] >= floor]
    flat.sort(key=lambda c: -c["adds"])
    return flat[:top]


def by_position(roster: pl.DataFrame, free: pl.DataFrame,
                settings: LeagueSettings, board: pl.DataFrame | None = None,
                deep: int = 3, n_sims: int = 800) -> dict[str, list[dict]]:
    """The top few at EVERY position, not just the best claims overall.

    THE DRAFT BOARD PROBLEM AGAIN. Waivers are a queue: the man you want can be
    gone before your priority comes up, and a single ranked list leaves you
    with nothing to do when he is. So every position carries its next few, the
    way the draft shortlist does -- the answer to "he got claimed" should be on
    screen before he gets claimed.

    Cheaper per man than `claims` (fewer simulations) because this is a list to
    browse rather than the one recommendation.
    """
    base = _lineup_points(roster, settings)
    full = roster.height >= settings.roster_size
    cut = (_cuts(roster, settings, base, board, free, n_sims)
           if full and roster.height else [])

    out: dict[str, list[dict]] = {}
    for pos in CLAIMABLE:
        pool = free.filter(pl.col("position") == pos)
        if not pool.height:
            continue
        got = claims(roster, pool, settings, top=deep, board=board,
                     n_sims=n_sims, floor=None, shortlist=deep + 3, cuts=cut,
                     pool_all=free)
        if got:
            out[pos] = got
    return out


def rest(free: pl.DataFrame, pos: str, skip: set[str],
         limit: int = 40) -> list[dict]:
    """The rest of the wire at one position, unpriced.

    The four priced men are the RECOMMENDATION; this is the wire itself, which
    is a different thing and worth having on screen. Nothing here is claimed to
    be worth adding -- pricing a man is a season simulation and doing it for
    three hundred names to fill a column would be a waste of four seconds.
    They price on click.
    """
    pool = (free.filter((pl.col("position") == pos)
                        & ~pl.col("player_id").is_in(list(skip)))
                .sort("projected_points", descending=True, nulls_last=True)
                .head(limit))
    return [{
        "player_id": r["player_id"],
        "player_name": r["player_name"],
        "position": r["position"],
        "projected_points": round(float(r.get("projected_points") or 0.0), 1),
        "ecr": r.get("ecr"),
        "owned": _num(r.get("percent_owned")),
    } for r in pool.iter_rows(named=True)]


def one(roster: pl.DataFrame, free: pl.DataFrame, settings: LeagueSettings,
        board: pl.DataFrame, player_id: str, n_sims: int = 2000) -> dict | None:
    """Price a single man off the wire, on demand.

    Same measure as the ranked list, run for somebody you asked about rather
    than somebody we suggested. A browse list you cannot interrogate is a
    ranking with extra steps.
    """
    got = claims(roster, free.filter(pl.col("player_id") == player_id),
                 settings, top=1, board=board, n_sims=n_sims, floor=None,
                 shortlist=1, pool_all=free)
    if not got:
        return None
    c = got[0]
    c["why"] = why(c, roster, settings, free)
    return c


def why(claim: dict, roster: pl.DataFrame, settings: LeagueSettings,
        free: pl.DataFrame | None = None, nxt: dict | None = None) -> list[dict]:
    """Why this man is worth a claim, from the numbers that ranked him.

    Same rule as everywhere else in this project: every line carries the figure
    that produced it, and nothing is asserted that was not computed. A waiver
    tool that says "great upside" is describing its own enthusiasm.

    The lines are ordered by what actually decides the claim -- what he does to
    your lineup first, what he costs second, what the market thinks last.
    """
    out: list[dict] = []
    pos = claim["position"]
    starters, _ = optimal_lineup(roster, settings)
    at = (starters.filter(pl.col("position") == pos)
          if starters.height else roster.head(0))
    worst = float(at["projected_points"].min()) if at.height else None
    proj = float(claim.get("projected_points") or 0.0)

    # 1. DOES HE CRACK YOUR LINEUP. The half of the question every other
    #    waiver list leaves out.
    if worst is None:
        out.append({"k": "wire_adds", "stat": "empty", "value": proj, "text":
                    f"You are not starting anybody at {pos}, so he does not "
                    f"have to be good — he has to exist. Everything below is "
                    f"measured against fielding nobody."})
    elif proj > worst:
        beaten = at.sort("projected_points").head(1)
        out.append({"k": "wire_adds", "stat": f"+{round(proj - worst)}",
                    "value": proj - worst, "text":
                    f"He starts. Projected {round(proj - worst)} points over "
                    f"the season above {beaten['player_name'][0]}, who is your "
                    f"weakest {pos} starter today."})
    else:
        out.append({"k": "wire_adds", "stat": "depth", "value": proj - worst, "text":
                    f"He does not beat the {pos}s you already start. What he "
                    f"is worth is the weeks one of them is out — which is why "
                    f"the number below is small but not zero."})

    # 2. WHAT HE IS WORTH, from the simulation rather than the projection.
    weekly = claim["adds"] / 17.0
    out.append({"k": "wire_adds", "stat": f"+{claim['adds']}",
                "value": claim["adds"], "text":
                f"What he adds across a simulated season once the lineup is "
                f"re-solved around him every week — about "
                f"{weekly:.1f} points a week. This is the number he was "
                f"ranked on, not his projection."})

    # 3. WHAT HE COSTS. A claim on a full roster is a trade with the wire.
    #
    #    AND SOMETIMES THE COST IS NEGATIVE, which is not a rounding artefact.
    #    The cheapest man to cut is occasionally worth LESS than the free agent
    #    who would replace him -- a rostered kicker the wire beats by three
    #    points a season is the common case, and it is the streaming edge
    #    arriving from the other direction. Saying "this costs you Jake Bates"
    #    about a move that gains you points would be the screen lying to be
    #    consistent.
    if claim.get("drop_name"):
        cost = claim["drop_cost"]
        who = roster.filter(pl.col("player_id") == claim.get("drop_id"))
        their_pos = who["position"][0] if who.height else "position"
        if cost > 0.5:
            out.append({"k": "wire_drop", "stat": f"−{cost:.1f}",
                        "value": -cost, "text":
                        f"Your roster is full, so this costs you "
                        f"{claim['drop_name']} — the man whose loss your "
                        f"lineup feels least, which is rarely the one with the "
                        f"smallest projection. Already subtracted above."})
        else:
            out.append({"k": "wire_drop", "stat": f"+{abs(cost):.1f}",
                        "value": abs(cost), "text":
                        f"The spot comes from {claim['drop_name']}, and losing "
                        f"him is not a cost: the best free {their_pos} is worth "
                        f"{abs(cost):.1f} points MORE than he is over a season. "
                        f"Drop him whether or not you make this claim."})

    # 4. HOW SAFE. A wire pickup is usually a bet on a range, not a mean.
    if claim.get("floor") is not None and claim.get("ceiling") is not None:
        out.append({"k": "range", "stat": f"{round(claim['floor'])}–{round(claim['ceiling'])}",
                    "value": claim["ceiling"] - claim["floor"], "text":
                    f"Season floor to ceiling, the middle 60% of simulated "
                    f"outcomes. A wide band on the wire is the point: you are "
                    f"paying nothing for the upside."})

    # 5. WHAT LOSING THE CLAIM COSTS. Measured against the NEXT MAN IN THE
    #    QUEUE, not against the highest projection left on the wire -- those
    #    are different players and only one of them answers the question.
    #    Comparing on projection had the top quarterback claim reporting a gap
    #    of "+-33 vs wire" against a man with a bigger number who is worth less
    #    to this roster, and then calling that a small drop-off.
    if nxt is not None:
        cost = claim["adds"] - nxt["adds"]
        out.append({"k": "wire_next", "stat": f"−{max(cost, 0.0):.1f}", "value": cost,
                    "text":
                    f"If somebody claims him first your next option is "
                    f"{nxt['player_name']} at +{nxt['adds']}, so losing this "
                    f"claim costs {round(cost, 1)} points. "
                    + ("Not worth spending waiver priority on."
                       if cost < 3 else
                       "That is the gap a priority buys you.")})

    # 6. WHAT THE MARKET THINKS. Two independent readings, both facts.
    if claim.get("owned") is not None:
        own = claim["owned"]
        out.append({"k": "wire_own", "stat": f"{round(own)}%", "value": own, "text":
                    (f"Rostered in {round(own)}% of ESPN leagues and free in "
                     f"yours — the rest of the format has already decided he "
                     f"is worth a spot."
                     if own >= 40 else
                     f"Rostered in only {round(own)}% of ESPN leagues. Nobody "
                     f"else wants him either, so the case for him has to come "
                     f"from your roster, not from the market.")})
    if claim.get("ecr"):
        out.append({"k": "adp", "stat": f"#{round(float(claim['ecr']))}",
                    "value": float(claim["ecr"]), "text":
                    "Where the draft room priced him. Nobody in your league "
                    "rostered him, so this is what the market paid for a man "
                    "it then let go."})
    return out


def note(roster: pl.DataFrame, settings: LeagueSettings, full: bool,
         found: int) -> str:
    """One sentence about what this list is and is not, computed not asserted."""
    if not found:
        return ("Nothing on the wire improves your starting lineup. That is the "
                "usual answer in a settled league and it is worth trusting -- "
                "a claim that does not change your eleven is a roster move for "
                "its own sake.")
    if full:
        return ("Your roster is full, so every claim below is priced WITH the "
                "drop it forces. The man being cut is the one whose loss costs "
                "your lineup least, which is rarely the one with the smallest "
                "projection.")
    return ("You have a spare roster spot, so these cost you nothing but the "
            "claim itself.")
