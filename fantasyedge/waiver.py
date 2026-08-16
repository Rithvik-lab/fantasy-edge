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


def _lineup_points(roster: pl.DataFrame, settings: LeagueSettings) -> float:
    if not roster.height:
        return 0.0
    starters, _ = optimal_lineup(roster, settings)
    return float(starters["projected_points"].fill_null(0.0).sum()) \
        if starters.height else 0.0


def claims(roster: pl.DataFrame, free: pl.DataFrame, settings: LeagueSettings,
           top: int = 12, limit: int | None = None,
           board: pl.DataFrame | None = None, n_sims: int = 1500) -> list[dict]:
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
    """
    if not free.height:
        return []
    limit = limit or settings.roster_size
    base = _lineup_points(roster, settings)
    full = roster.height >= limit

    # Who goes if somebody has to. The worst man on the roster BY LINEUP
    # IMPACT, not by projection: the last bench receiver is usually a smaller
    # loss than a kicker, however the numbers look side by side.
    cuts: list[tuple[str, str, float]] = []
    if full and roster.height:
        for r in roster.iter_rows(named=True):
            without = roster.filter(pl.col("player_id") != r["player_id"])
            cuts.append((r["player_id"], r["player_name"],
                         base - _lineup_points(without, settings)))
        cuts.sort(key=lambda c: c[2])

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
    for cheap, r in ranked[:SHORTLIST]:
        gain = cheap
        if board is not None:
            v = evaluate(roster, [drop_id] if drop_id else [], [r["player_id"]],
                         settings, board, n_sims=n_sims, free_agents=free)
            gain = v.delta_median
        if gain < MIN_ADD:
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
        })

    out.sort(key=lambda c: -c["adds"])
    return out[:top]


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
