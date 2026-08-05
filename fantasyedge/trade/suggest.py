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
from fantasyedge.trade.evaluate import evaluate

# How generous the offer has to look from across the table. Zero means "they
# break even"; a real offer needs to be visibly good or it gets ignored.
THEIR_MIN_GAIN = 5.0
# Ours, on the honest scale. Below this it is not worth the message.
OUR_MIN_GAIN = 6.0
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
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "team_id": self.team_id,
            "team_name": self.team_name,
            "give": self.give,
            "get": self.get,
            "our_gain": round(self.our_gain, 1),
            "their_gain": round(self.their_gain, 1),
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


def _packages(df: pl.DataFrame, max_size: int) -> list[tuple[str, ...]]:
    ids = df["player_id"].to_list()
    out: list[tuple[str, ...]] = [(i,) for i in ids]
    if max_size >= 2:
        out += list(combinations(ids, 2))
    return out


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
) -> list[Offer]:
    """Best offers to send one opponent."""
    if not mine.height or not theirs.height:
        return []

    pv = dict(zip(board["player_id"].to_list(),
                  board["perceived_value"].fill_null(0.0).to_list()))

    # Everything the cheap pass needs, pulled out of polars ONCE. Inside the
    # loop this is plain python and stays that way.
    mine_rows = _as_rows(mine)
    their_rows = _as_rows(theirs)
    limit = settings.roster_size

    base_ours = _fast_lineup(list(mine_rows.values()), settings)
    base_theirs = _fast_lineup(list(their_rows.values()), settings)

    give_sets = _packages(mine, max_out)
    get_sets = _packages(theirs, max_in)

    # --- pass 1 + 2: prune on their scale, rank on ours ------------------
    scored = []
    for g in give_sets:
        g_pv = sum(pv.get(i, 0.0) for i in g)
        kept_ours = [v for pid, v in mine_rows.items() if pid not in g]
        out_rows = [mine_rows[i] for i in g if i in mine_rows]

        for k in get_sets:
            # What they gain, as they see it: they receive our package and
            # give up theirs. Checked first because it is one subtraction and
            # it eliminates most of the space.
            if g_pv - sum(pv.get(i, 0.0) for i in k) < THEIR_MIN_GAIN:
                continue

            in_rows = [their_rows[i] for i in k if i in their_rows]
            after_ours = kept_ours + in_rows
            if len(after_ours) > limit:
                after_ours = sorted(after_ours, key=lambda r: -r[1])[:limit]
            cheap = _fast_lineup(after_ours, settings) - base_ours
            if cheap <= 0:
                continue

            # Their roster has to actually work afterwards, or they will see
            # the hole even if the arithmetic flatters them.
            after_theirs = [v for pid, v in their_rows.items()
                            if pid not in k] + out_rows
            if _fast_lineup(after_theirs, settings) < base_theirs:
                continue

            scored.append((cheap, g_pv - sum(pv.get(i, 0.0) for i in k), g, k))

    if not scored:
        return []

    scored.sort(key=lambda r: -r[0])
    offers: list[Offer] = []

    # --- pass 3: the real verdict, survivors only ------------------------
    for cheap, their_gain, g, k in scored[:SHORTLIST]:
        v = evaluate(mine, list(g), list(k), settings, board, n_sims=SCAN_SIMS)
        if v.delta_median < OUR_MIN_GAIN:
            continue
        offers.append(Offer(
            team_id=team_id,
            team_name=team_name,
            give=v.give,
            get=v.get,
            our_gain=v.delta_median,
            their_gain=their_gain,
            win_probability=v.win_probability,
            naive_delta=v.naive_value_delta,
            note=v.note,
        ))

    offers.sort(key=lambda o: -o.our_gain)
    return offers[:top]


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
) -> list[Offer]:
    """Scan every opponent. `rosters` excludes yours."""
    b = market.perceived(board, observed, through_week)
    names = names or {}

    out: list[Offer] = []
    for tid, roster in rosters.items():
        out += for_team(mine, roster, b, settings, tid,
                        names.get(tid, f"Team {tid}"), top=per_team)

    out.sort(key=lambda o: -o.our_gain)
    return out[:top]
