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

# UPSIDE, AND WHY IT HAS TO BE PRICED SEPARATELY.
#
# `simulate_lineup` fixes the starting order in ADVANCE, by season rate, and
# only availability moves week to week. That was the right fix -- it removed a
# manager who knows on Wednesday what happens on Sunday -- but it also removed
# something real: you DO promote a man who breaks out, not with Sunday's
# results but with September's. Inside the simulation a wire pickup who has a
# monster season never leaves the bench, so his ceiling is worth nothing to
# him and the ranking quietly prefers the safe useful add over the flier.
#
# That is backwards for waivers specifically. A bench player's downside is
# capped -- you do not start a bust -- while his upside is not, because a hit
# becomes a starter. `draft/engine` already says exactly this about bench picks
# and tolerates more variance there for it.
#
# So the ceiling is priced as its own question, of the LINEUP rather than the
# simulation: with his 80th-percentile season in place of his median, how much
# does he add to the eleven you would field? Zero if even his good year does
# not crack it.
#
# The weight is the quantile's own exceedance probability -- p80 is by
# definition the outcome he beats one year in five -- so this is an expected
# value and not a taste setting.
P_HIT = 0.20


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
        at_median = _lineup_points(added, settings)
        cheap = at_median - base

        # AND WHAT HE IS WORTH IF HE HITS, which is a different question and
        # the one a wire pickup is actually about. See UPSIDE below: the season
        # simulation structurally cannot answer it, so it is asked here, of the
        # lineup, with his ceiling in place of his median.
        #
        # MEASURED AGAINST THE SAME ROSTER HE ARRIVES ON, not against the one
        # before the drop. Against `base` it came back 0.0 for every skill
        # player on the wire, because the drop empties a slot and the
        # deterministic solve prices an empty slot at zero -- cutting a kicker
        # cost 141 points, which buried a receiver's ceiling completely. Both
        # sides carry the same drop, so it cancels and what is left is the only
        # thing being asked about: the difference his good season makes.
        # NOT AT KICKER OR DEFENCE. Their band is not measured -- `models.kdst`
        # gives every one of them the same flat ±15% around a projection,
        # because season-long finish at those positions is close to
        # unpredictable in August and the model says so honestly. Treating that
        # placeholder as a ceiling turned it into signal: every defence on the
        # wire came back with 47 points of "upside" and the whole list sorted
        # kickers and defences to the top of it. There is a real weekly edge at
        # both, it is a matchup, and it arrives with games.
        up = 0.0
        p80 = r.get("season_p80") if r["position"] not in ("K", "DST") else None
        if p80:
            hit = one.with_columns(pl.lit(float(p80)).alias("projected_points"))
            h = pl.concat([roster.select(cols), hit.select(cols)], how="vertical")
            if drop_id:
                h = h.filter(pl.col("player_id") != drop_id)
            up = max(_lineup_points(h, settings) - at_median, 0.0)
        ranked.append((cheap + P_HIT * up, cheap, up, r))
    ranked.sort(key=lambda x: -x[0])

    # --- pass two: price the survivors properly --------------------------
    from fantasyedge.trade.evaluate import evaluate

    out = []
    for score, cheap, up, r in ranked[:shortlist]:
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
        # What he is worth AS DEPTH plus what he is worth IF HE HITS, times the
        # odds he does. Two disjoint things: the first is the weeks somebody
        # ahead of him cannot play, the second is him taking the job outright.
        worth = gain + P_HIT * up

        # `floor=None` browses rather than recommends. The positional lists
        # have to show the next man up even when he is not worth a claim today,
        # because the whole point of them is what to do when your first choice
        # is gone before your priority comes up.
        if floor is not None and worth < floor:
            continue
        out.append({
            "player_id": r["player_id"],
            "player_name": r["player_name"],
            "position": r["position"],
            "ecr": r.get("ecr"),
            "projected_points": round(float(r.get("projected_points") or 0.0), 1),
            "adds": round(gain, 1),
            # What the ceiling is worth, and the two combined -- the number the
            # list is ordered on. Both travel so the card can show its working
            # rather than quoting one total nobody can take apart.
            "upside": round(up, 1),
            "worth": round(worth, 1),
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

    out.sort(key=lambda c: -c["worth"])
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
        flat = [c for c in flat if c["worth"] >= floor]
    flat.sort(key=lambda c: -c["worth"])
    return flat[:top]


def by_position(roster: pl.DataFrame, free: pl.DataFrame,
                settings: LeagueSettings, board: pl.DataFrame | None = None,
                deep: int = 3, n_sims: int = 800,
                cuts: list[tuple[str, str, float]] | None = None
                ) -> dict[str, list[dict]]:
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
    # The cut order is a property of the roster, so a caller who already has it
    # -- the plan needs it whether or not the roster is full -- hands it over
    # rather than paying for eighteen more simulations.
    cut = cuts if cuts is not None else (
        _cuts(roster, settings, base, board, free, n_sims)
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


# A man this cheap to cut is not costing you anything to keep, either. Above
# it a drop is a real decision; below it the roster spot is the only thing he
# is doing for you.
DEAD_WEIGHT = 0.5


def plan(roster: pl.DataFrame, free: pl.DataFrame, settings: LeagueSettings,
         board: pl.DataFrame, groups: dict[str, list[dict]],
         cuts: list[tuple[str, str, float]], n_sims: int = 2000) -> dict:
    """What to actually do, this minute, with reasons.

    THE LIST IS NOT THE ANSWER. Four names at six positions with a number
    beside each is a screen you still have to think in front of, and the
    thinking is the same every week: is anybody on this roster worth less than
    what is free, and if so who replaces him. That is a question with an answer,
    so it gets answered rather than laid out.

    Two shapes of move come out of it:

      SWAP -- somebody on your roster is beaten by a free agent at his own
      position. This is the move nobody makes, because a man you drafted feels
      like an asset and the wire feels like scraps; the simulation does not
      care where he came from. His kicker is worth 2.9 points a season LESS
      than the best free one, and that is a claim worth making on a roster with
      no holes and nothing obviously wrong with it.

      FILL -- a spare roster spot and a man on the wire worth more than an
      empty seat. No drop, no argument.

    Every move is re-priced as the swap it actually is rather than added up
    from two numbers that were measured separately. `adds` was measured with a
    different drop in mind, and `cost` was measured with nobody arriving; the
    pair of them is a third question.
    """
    from fantasyedge.trade.evaluate import evaluate

    full = roster.height >= settings.roster_size
    moves: list[dict] = []

    # --- swaps: a man on the roster the wire beats at his own position ----
    #
    # A CLAIM CAN ONLY BE SPENT ONCE. Two men at the same position both worth
    # dropping is the ordinary case, and taking the best free agent for each of
    # them proposed the same arrival twice -- a plan you cannot carry out. Each
    # move takes the next man down the queue, which is what that queue is for.
    used: set[str] = set()
    for pid, name, c in cuts:
        if c > DEAD_WEIGHT:
            continue
        row = roster.filter(pl.col("player_id") == pid)
        if not row.height:
            continue
        pos = row["position"][0]
        best_at = next((m for m in groups.get(pos, [])
                        if m["adds"] > 0 and m["player_id"] not in used), None)
        if best_at is None:
            continue
        used.add(best_at["player_id"])
        v = evaluate(roster, [pid], [best_at["player_id"]], settings, board,
                     n_sims=n_sims, free_agents=free)
        gain = v.delta_mean
        if gain <= 0:
            continue
        moves.append({
            "kind": "swap",
            "add": best_at,
            "drop": {"player_id": pid, "player_name": name, "position": pos,
                     "cost": round(c, 1)},
            "gain": round(gain, 1),
            "why": (f"{name} is the cheapest man on your roster to lose — "
                    + (f"losing him costs {c:.1f} points"
                       if c > 0 else
                       f"the best free {pos} is worth {abs(c):.1f} points MORE "
                       f"than he is")
                    + f". {best_at['player_name']} is that {pos}, and making "
                      f"the swap is worth {gain:.1f} points across the season."),
        })

    # --- fills: a spare spot and somebody worth more than an empty seat ---
    if not full:
        for c in best(groups, top=3):
            if c["player_id"] in used:
                continue
            used.add(c["player_id"])
            moves.append({
                "kind": "fill",
                "add": c,
                "drop": None,
                "gain": c["adds"],
                "why": (f"You have {settings.roster_size - roster.height} spare "
                        f"roster spot"
                        + ("s" if settings.roster_size - roster.height > 1 else "")
                        + f", so {c['player_name']} costs you nothing but the "
                          f"claim. He is worth {c['adds']:.1f} points across "
                          f"the season."),
            })
            break

    moves.sort(key=lambda m: -m["gain"])

    if not moves:
        head = "Nothing to do."
        note = ("Nobody on the wire beats anybody on your roster, and you have "
                "nothing spare to cut. That is the usual answer in a settled "
                "league and it is worth trusting — a claim that does not change "
                "your lineup is a roster move for its own sake.")
    else:
        head = (f"{len(moves)} move{'s' if len(moves) > 1 else ''} worth making")
        note = ("Priced as swaps, not as two separate numbers added together. "
                "Each one is what your season is worth after the move minus "
                "what it is worth now.")

    return {"headline": head, "note": note, "moves": moves,
            "dead": [{"player_id": p, "player_name": n, "cost": round(c, 1)}
                     for p, n, c in cuts if c <= DEAD_WEIGHT]}


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
    him = claim["player_name"]
    starters, _ = optimal_lineup(roster, settings)
    at = (starters.filter(pl.col("position") == pos)
          if starters.height else roster.head(0))
    worst = float(at["projected_points"].min()) if at.height else None
    proj = float(claim.get("projected_points") or 0.0)

    # EVERY LINE SAYS WHO IT IS ABOUT. The panel scrolls, the name is at the
    # top of it, and he asked "who is he?" of a card three reasons deep --
    # which is the whole answer: prose about "him" is unreadable the moment it
    # is separated from the one place his name appears.
    #
    # A DEFENCE AND A KICKER DO NOT MISS GAMES. They are the two positions
    # where "the weeks one of them is out" is simply false -- you start one
    # every week and the only week you do not have him is his bye. Saying it
    # anyway produced "he does not beat the DSTs you already start", plural,
    # about a one-slot position, explaining injury cover for a team.
    flat = pos in ("K", "DST")
    mine_at = at["player_name"][0] if at.height else None
    # Whether he walks into the lineup or waits behind somebody, decided once
    # so the lines below cannot contradict each other. They did: the kicker
    # card said "Butker starts, two points above Jake Bates" and then called
    # what he adds "the bye week you would otherwise field nobody".
    starts = worst is None or proj > worst

    # 1. DOES HE CRACK YOUR LINEUP. The half of the question every other
    #    waiver list leaves out.
    if worst is None:
        out.append({"k": "wire_adds", "stat": "empty", "value": proj, "text":
                    f"You are not starting anybody at {pos}, so {him} does not "
                    f"have to be good — he has to exist. Everything below is "
                    f"measured against fielding nobody."})
    elif proj > worst:
        beaten = at.sort("projected_points").head(1)
        out.append({"k": "wire_adds", "stat": f"+{round(proj - worst)}",
                    "value": proj - worst, "text":
                    f"{him} starts. Projected {round(proj - worst)} points over "
                    f"the season above {beaten['player_name'][0]}, who is your "
                    f"weakest {pos} starter today."})
    elif flat:
        out.append({"k": "wire_adds", "stat": "backup", "value": proj - worst,
                    "text":
                    f"{him} does not beat {mine_at}, who you start every week. "
                    f"A {pos} never misses a game, so the only week this is "
                    f"worth anything is {mine_at}'s bye — and that is the whole "
                    f"of the number below."})
    else:
        many = at.height > 1
        out.append({"k": "wire_adds", "stat": "depth", "value": proj - worst,
                    "text":
                    f"{him} does not beat the {pos}"
                    + (f"s you already start" if many
                       else f" you already start, {mine_at}")
                    + f". What he is worth is the weeks "
                    + ("one of them is" if many else "he is")
                    + f" out — which is why the number below is small but not "
                      f"zero."})

    # 2. WHAT HE IS WORTH, from the simulation rather than the projection.
    weekly = claim["adds"] / 17.0
    out.append({"k": "wire_adds", "stat": f"+{claim['adds']}",
                "value": claim["adds"], "text":
                (f"What {him} adds across a simulated season, once the lineup "
                 f"is re-solved around him every week"
                 if starts else
                 f"What {him} adds as DEPTH across a simulated season — "
                 + ("the bye week you would otherwise field nobody"
                    if flat else
                    "the weeks somebody ahead of him cannot play"))
                + f", about {weekly:.1f} points a week. Not his projection, "
                  f"which is a fact about him rather than about your team."})

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

    # 3b. WHAT HE IS WORTH IF HE HITS. The half of a wire pickup the season
    #     simulation cannot see, because it fixes the starting order in advance
    #     and a man who breaks out there never leaves the bench.
    if claim.get("upside"):
        up = claim["upside"]
        out.append({"k": "wire_upside", "stat": f"↑{round(up)}", "value": up,
                    "text":
                    f"If he hits his ceiling he does not just cover for "
                    f"somebody — he starts, and your lineup is {round(up)} "
                    f"points better for it. That happens about one year in "
                    f"five, which is what puts {round(P_HIT * up, 1)} of it in "
                    f"the total above. It is why a flier can outrank a safer "
                    f"man who adds more depth."})

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
    # The COUNT, not "a spare roster spot" -- the plan above this says "you
    # have 2 spare roster spots" and two sentences disagreeing about the same
    # roster on the same screen is the kind of thing that makes a reader stop
    # trusting both of them.
    spare = settings.roster_size - roster.height
    return (f"You have {spare} spare roster spot{'s' if spare != 1 else ''}, so "
            f"these cost you nothing but the claim itself.")
