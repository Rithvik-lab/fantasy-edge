"""What your team is good at, what it is not, and how you got here.

THE QUESTION THIS ANSWERS

A roster grade is one number and one number cannot be acted on. "You are a 104"
tells you nothing about whether to stream a quarterback, chase a tight end, or
sit still. The useful shape is comparative and per position: against the
eleven other teams in this league, where are you strong, and where are you
about to lose a week you should have won?

EVERY BENCHMARK IS THE LEAGUE, NOT AN ABSOLUTE

A 240-point tight end is excellent in a league where the median starter scores
150 and unremarkable where he scores 230. So each of your starters is scored
against the players who will actually start at that slot across the league --
the top `slots x teams` at his position -- and the answer comes back as a
percentile. That is what makes "strength" mean something you can trade on.

THE DRAFT REPORT IS SEPARATE ON PURPOSE

How you drafted and what you ended up with are different questions. A pick can
be good value and a bad fit; a roster can be strong and badly drafted, because
other people reached and the board fell to you. Value against ADP measures the
first, positional percentile the second, and mixing them produces a number that
answers neither.
"""

from __future__ import annotations

import polars as pl

from fantasyedge.draft.session import optimal_lineup
from fantasyedge.league import LeagueSettings

# A bye is only a problem when it takes out players you would have STARTED.
# Two backup tight ends sharing week nine is not a story.
BYE_WEEKS = 18

# Positions where "you are below the league" is not a problem worth acting on,
# and where "you beat the room" is not a compliment either. Kicker and defence
# are nearly flat by construction: being 30th percentile at kicker costs about
# a point a week and no roster move fixes it, and beating ADP on one is a fact
# about when the room took kickers rather than about you.
NOT_WORTH_FIXING = frozenset({"K", "DST"})


def _league_starters(board: pl.DataFrame, settings: LeagueSettings,
                     position: str) -> pl.Series:
    """Everyone who will realistically start at this position, league-wide."""
    slots = settings.lineup.get(position, 0)
    if position in settings.flex_eligible:
        # FLEX pulls from the same pool, so the startable set is deeper than
        # the dedicated slots alone suggest.
        slots += settings.lineup.get("FLEX", 0) / max(
            len(settings.flex_eligible), 1)
    n = max(int(round(slots * settings.n_teams)), settings.n_teams)
    return (board.filter(pl.col("position") == position)
                 .sort("projected_points", descending=True, nulls_last=True)
                 .head(n)["projected_points"])


def positional_strength(roster: pl.DataFrame, board: pl.DataFrame,
                        settings: LeagueSettings) -> list[dict]:
    """Per position: your starters against the league's startable pool.

    Percentile of the MEDIAN startable player, not of the whole board -- being
    better than a waiver-wire tight end is not a strength, being better than
    the tight end your opponent starts is.
    """
    if not roster.height:
        return []
    starters, _ = optimal_lineup(roster, settings)
    out = []

    for pos in [p for p in settings.lineup if p not in ("FLEX", "SUPERFLEX")]:
        pool = _league_starters(board, settings, pos)
        if not pool.len():
            continue
        mine = starters.filter(pl.col("position") == pos)
        if not mine.height:
            out.append({
                "position": pos, "have": 0, "need": settings.lineup[pos],
                "points": 0.0, "league_median": float(pool.median() or 0),
                "percentile": 0.0, "edge": -float(pool.median() or 0),
            })
            continue

        pts = float(mine["projected_points"].fill_null(0).sum())
        per = pts / mine.height
        med = float(pool.median() or 0)
        # Where the AVERAGE of your starters lands inside the startable pool.
        pct = float((pool < per).sum()) / pool.len()
        out.append({
            "position": pos,
            "have": mine.height,
            "need": settings.lineup[pos],
            "points": round(pts, 1),
            "per_starter": round(per, 1),
            "league_median": round(med, 1),
            "percentile": round(pct, 3),
            # Points above or below what an average team starts here. This is
            # the number that is worth trading on.
            "edge": round((per - med) * mine.height, 1),
        })

    return sorted(out, key=lambda r: -r["edge"])


def team_byes(season: int) -> dict[str, int]:
    """Team -> its bye week, from the published schedule.

    Free and known months ahead: a bye is simply the week a team has no game.
    Derived rather than looked up in a table someone maintains by hand.
    """
    try:
        import nflreadpy as nfl

        sched = nfl.load_schedules(seasons=[season])
        if not sched.height:
            return {}
        played = (pl.concat([
            sched.select([pl.col("home_team").alias("team"), "week"]),
            sched.select([pl.col("away_team").alias("team"), "week"]),
        ]).filter(pl.col("week") <= BYE_WEEKS))
        out: dict[str, int] = {}
        for team in played["team"].unique().to_list():
            weeks = set(played.filter(pl.col("team") == team)["week"].to_list())
            gap = [w for w in range(1, BYE_WEEKS + 1) if w not in weeks]
            if len(gap) == 1:
                out[team] = gap[0]
        return out
    except Exception:
        return {}


def with_byes(roster: pl.DataFrame, season: int) -> pl.DataFrame:
    """Attach `bye_week` using the depth chart for team affiliation.

    The board carries no team, because a draft board never needed one. The
    depth chart does, and it already joins on player_id -- so the two together
    answer a question neither could alone.
    """
    if "bye_week" in roster.columns or not roster.height:
        return roster
    byes = team_byes(season)
    if not byes:
        return roster
    try:
        from fantasyedge.data import depth

        chart = depth.depth_chart(season)
        if not chart.height:
            return roster
        return (roster.join(chart.select(["player_id", "chart_team"]),
                            on="player_id", how="left")
                      .with_columns(pl.col("chart_team")
                                    .replace_strict(byes, default=None,
                                                    return_dtype=pl.Int32)
                                    .alias("bye_week")))
    except Exception:
        return roster


def bye_conflicts(roster: pl.DataFrame, settings: LeagueSettings) -> list[dict]:
    """Weeks where byes take out more starters than you can cover."""
    if "bye_week" not in roster.columns or not roster.height:
        return []
    starters, _ = optimal_lineup(roster, settings)
    if not starters.height:
        return []
    hits = []
    for wk in range(1, BYE_WEEKS + 1):
        out = starters.filter(pl.col("bye_week") == wk)
        if out.height >= 2:
            hits.append({
                "week": wk,
                "count": out.height,
                "players": out["player_name"].to_list(),
                "points": round(float(out["projected_points"].fill_null(0).sum()), 1),
            })
    return sorted(hits, key=lambda r: -r["points"])[:4]


def draft_report(picks: list[dict], board: pl.DataFrame,
                 my_ids: list[str]) -> dict:
    """Where you took each man against where the room had him.

    Value against ADP, and nothing else. It deliberately does not know whether
    the pick fit your roster -- a steal at a position you were already deep at
    is still a steal, and conflating the two produces a number that answers
    neither question.
    """
    if not picks or not my_ids:
        return {"picks": [], "total_edge": 0.0}

    adp = dict(zip(board["player_id"].to_list(), board["ecr"].to_list()))
    names = dict(zip(board["player_id"].to_list(), board["player_name"].to_list()))
    pos = dict(zip(board["player_id"].to_list(), board["position"].to_list()))

    rows, edge = [], 0.0
    for p in picks:
        pid = p.get("player_id")
        if pid not in set(my_ids):
            continue
        # KICKERS AND DEFENCES ARE NOT VALUE PICKS. Everyone takes one in the
        # last two rounds and the order inside those rounds is close to random,
        # so "you beat the room by 40 on your kicker" is a fact about when the
        # room happened to take kickers, not about you. Left in, they were also
        # the largest edges on the card, because deep ADP is the noisiest part
        # of the board -- the same trap that made them the biggest bars on the
        # positional chart.
        if pos.get(pid) in NOT_WORTH_FIXING:
            continue
        where = p.get("overall")
        market = adp.get(pid)
        if where is None or market is None:
            continue
        # Positive = he was still there later than the room said he would be.
        d = float(market) - float(where)
        edge += d
        rows.append({
            "overall": int(where),
            "player_id": pid,
            "player_name": names.get(pid, "?"),
            "position": pos.get(pid),
            "adp": round(float(market), 1),
            "edge": round(d, 1),
            "verdict": "steal" if d >= 12 else "reach" if d <= -12 else "market",
        })

    rows.sort(key=lambda r: r["overall"])
    return {
        "picks": rows,
        "total_edge": round(edge, 1),
        "steals": sum(1 for r in rows if r["verdict"] == "steal"),
        "reaches": sum(1 for r in rows if r["verdict"] == "reach"),
    }


def _ordinal(pct: float) -> str:
    n = round(pct * 100)
    suffix = "th" if 11 <= n % 100 <= 13 else \
        {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def summarise(strength: list[dict], grade: dict) -> dict[str, list[str]]:
    """Two or three sentences each way, from the numbers above."""
    strong = [r for r in strength if r["edge"] >= 12]
    weak = [r for r in strength if r["edge"] <= -12]
    holes = [r for r in strength if r["have"] < r["need"]]

    good, bad = [], []
    for r in strong[:3]:
        good.append(f"{r['position']}: {round(r['edge']):+d} points on the "
                    f"average team, {_ordinal(r['percentile'])} percentile.")
    for r in reversed(weak[-3:]):
        bad.append(f"{r['position']}: {round(r['edge']):+d} against the average "
                   f"team, {_ordinal(r['percentile'])} percentile.")
    for r in holes:
        bad.append(f"{r['position']}: you have {r['have']} for "
                   f"{r['need']} starting slot(s).")

    if grade.get("season_floor") and grade.get("season_ceiling"):
        span = grade["season_ceiling"] - grade["season_floor"]
        if span:
            (good if span < 550 else bad).append(
                f"Season range {round(grade['season_floor'])}–"
                f"{round(grade['season_ceiling'])} — "
                f"{'tight, a dependable week to week team' if span < 550 else 'wide, this team swings'}.")

    if not good:
        good.append("No position clears the league average by a real margin.")
    if not bad:
        bad.append("No position is materially behind the league.")
    return {"strengths": good, "weaknesses": bad}


# ---------------------------------------------------------------------------
# The post-draft card
# ---------------------------------------------------------------------------

def league_comparison(board: pl.DataFrame, picks: list[dict],
                      settings: LeagueSettings,
                      my_ids: list[str],
                      my_slot: int | None = None,
                      with_shape: bool = True,
                      rosters: dict[int, list[str]] | None = None,
                      my_team_id: int | None = None) -> list[dict]:
    """Every team's starting strength, so yours has something to stand next to.

    A grade in isolation is a number you cannot act on. The same number beside
    eleven others is a standing, and standing is the only thing that decides
    whether to push or hold.

    `rosters` is who owns whom today. When it is available it wins outright --
    the pick log cannot see a waiver add or a trade, so after week one it
    describes a league that no longer exists.
    """
    mine = set(my_ids)
    by_slot: dict[int, list[str]] = {}

    if rosters:
        for tid, ids in rosters.items():
            if my_team_id is not None and int(tid) == int(my_team_id):
                continue
            by_slot[int(tid)] = list(ids)
        if mine:
            by_slot[-1] = list(mine)
        # Labelled by TEAM id here, not draft seat, because that is what the
        # bucket keys are and what team names are keyed by.
        return _rate(by_slot, board, settings,
                     my_team_id if my_team_id is not None else my_slot,
                     with_shape)

    if not picks:
        return []
    for p in picks:
        pid = p.get("player_id")
        if not pid or pid in mine:
            # YOUR players are identified by ownership, not by the seat the
            # pick number implies. In manual mode the two disagree -- you
            # record picks in sequence and your men land under whatever slot
            # the counter happened to be on, which had two different teams
            # coming back flagged as yours.
            continue
        by_slot.setdefault(int(p.get("slot") or 0), []).append(pid)
    # Your seat becomes the synthetic bucket, not an extra one beside it. Left
    # in, a twelve-team league came back with thirteen rows: eleven opponents,
    # your seat holding whatever the pick counter happened to attribute to it,
    # and you again.
    if my_slot is not None:
        by_slot.pop(int(my_slot), None)
    if mine:
        by_slot[-1] = list(mine)
    return _rate(by_slot, board, settings, my_slot, with_shape)


def _rate(by_slot: dict[int, list[str]], board: pl.DataFrame,
          settings: LeagueSettings, my_slot: int | None,
          with_shape: bool) -> list[dict]:
    """Score each bucket of ids as a starting lineup. Slot -1 means yours."""

    out = []
    for slot, ids in sorted(by_slot.items()):
        roster = board.filter(pl.col("player_id").is_in(ids))
        if not roster.height:
            continue
        starters, _ = optimal_lineup(roster, settings)
        pts = float(starters["projected_points"].fill_null(0).sum()) \
            if starters.height else 0.0
        floor = ceil = None
        if "season_p20" in starters.columns and starters.height:
            floor = float(starters["season_p20"].drop_nulls().sum())
            ceil = float(starters["season_p80"].drop_nulls().sum())
        # `expected_games` rides along so the hover can show what a man scores
        # in a week he plays. The lineup card next to it does, and two lists of
        # players in different units with neither one labelled is worse than
        # either choice on its own.
        keep = [c for c in ("player_id", "player_name", "position",
                            "projected_points", "expected_games")
                if c in starters.columns]
        out.append({
            "slot": slot if slot != -1 else (my_slot or 0),
            "mine": slot == -1,
            "starters": round(pts, 1),
            "floor": round(floor, 1) if floor else None,
            "ceiling": round(ceil, 1) if ceil else None,
            "players": roster.height,
            # Their lineup, for the hover. A bar telling you someone is ahead
            # is an assertion; showing who they start is the evidence, and it
            # is also the thing you would actually go looking for next.
            "lineup": (starters.select(keep).to_dicts()
                       if starters.height else []),
        })
        if with_shape:
            out[-1]["shape"] = team_shape(out[-1]["lineup"], board, settings)
    out.sort(key=lambda r: -r["starters"])
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return out


# The order a lineup card is written in, which is not the order anything sorts
# in. Sorting these by size answers "which number is biggest"; nobody reads a
# roster that way, and a chart that reorders itself between two visits is
# unreadable even when every bar is correct.
LINEUP_ORDER = ("QB", "RB", "WR", "TE", "FLEX", "DST", "K")


def in_lineup_order(rows: list[dict], key: str = "position") -> list[dict]:
    return sorted(rows, key=lambda r: (
        LINEUP_ORDER.index(r[key]) if r[key] in LINEUP_ORDER
        else len(LINEUP_ORDER)))


def tradeable(strength: list[dict]) -> list[dict]:
    """The positions worth arguing about, in lineup order.

    Kicker and defence come out. They are two thirds of the chart's height and
    none of its meaning: the curve at both is close to flat, so the gap between
    the best and worst is a fact about who drafted last, not about whose team is
    better. They stay on the roster, where they belong -- something has to fill
    the slot -- and out of every judgement about the team.
    """
    return in_lineup_order([r for r in strength
                            if r["position"] not in NOT_WORTH_FIXING])


def unfilled(starters: pl.DataFrame, settings: LeagueSettings) -> list[dict]:
    """Starting slots with nobody in them, in lineup order."""
    have: dict[str, int] = {}
    if starters.height and "slot" in starters.columns:
        for s in starters["slot"].to_list():
            base = (s or "").rstrip("0123456789") or (s or "")
            have[base] = have.get(base, 0) + 1
    out = []
    for slot, count in settings.lineup.items():
        missing = count - have.get(slot, 0)
        for i in range(max(missing, 0)):
            out.append({"slot": slot if count == 1 else f"{slot}{have.get(slot, 0) + i + 1}",
                        "position": slot})
    return in_lineup_order(out)

# A suggestion has to beat what you already start there. Otherwise the answer
# to "my receivers are weak" is a list of worse receivers, which is how a
# panel ends up recommending a washed veteran nobody would take.
MIN_UPGRADE = 15.0


def improvements(strength: list[dict], board: pl.DataFrame,
                 drafted: list[str], settings: LeagueSettings,
                 roster: pl.DataFrame | None = None,
                 top: int = 3) -> list[dict]:
    """What to do about the weak spots, and only where there is something to do.

    Two filters, both learned from the first version recommending a kicker
    upgrade and a receiver who is worse than the one already starting:

      the position has to matter -- K and DST are flat, so being behind there
      costs about a point a week and no move fixes it

      the player has to be an UPGRADE on your current starter at that slot,
      by a margin big enough to be worth a waiver claim
    """
    weak = [r for r in strength
            if r["edge"] < 0 and r["position"] not in NOT_WORTH_FIXING][:top]
    if not weak:
        return []

    # What you currently start at each position, to measure an upgrade against.
    have: dict[str, float] = {}
    if roster is not None and roster.height:
        starters, _ = optimal_lineup(roster, settings)
        for r in starters.iter_rows(named=True):
            pos = r["position"]
            pts = float(r.get("projected_points") or 0.0)
            have[pos] = min(have.get(pos, pts), pts)   # your WEAKEST there

    free = board.filter(~pl.col("player_id").is_in(drafted))
    out = []
    for w in weak:
        floor = have.get(w["position"], 0.0) + MIN_UPGRADE
        pool = (free.filter((pl.col("position") == w["position"])
                            & (pl.col("projected_points") > floor))
                    .sort("projected_points", descending=True, nulls_last=True)
                    .head(3))
        if not pool.height:
            continue
        out.append({
            "position": w["position"],
            "edge": w["edge"],
            "percentile": w["percentile"],
            "replaces": round(have.get(w["position"], 0.0), 1),
            "available": pool.select(
                [c for c in ("player_id", "player_name", "position", "ecr",
                             "projected_points", "vor") if c in pool.columns]
            ).to_dicts(),
        })
    return out


def team_shape(lineup: list[dict], board: pl.DataFrame,
               settings: LeagueSettings) -> list[dict]:
    """Why a team is where it is: its slots against the league's startable pool.

    Derived, so the sentence and the standing can never disagree. The first
    version of this screen described teams in prose nobody computed.
    """
    if not lineup:
        return []
    by: dict[str, float] = {}
    for p in lineup:
        if p["position"] in NOT_WORTH_FIXING:
            continue
        by[p["position"]] = by.get(p["position"], 0.0) + float(
            p.get("projected_points") or 0.0)

    out = []
    for pos, pts in by.items():
        pool = _league_starters(board, settings, pos)
        if not pool.len():
            continue
        n = sum(1 for p in lineup if p["position"] == pos)
        med = float(pool.median() or 0) * max(n, 1)
        out.append({"position": pos, "points": round(pts, 1),
                    "league": round(med, 1), "edge": round(pts - med, 1)})
    return sorted(out, key=lambda r: -r["edge"])


def sleepers(board: pl.DataFrame, my_ids: list[str], top: int = 5) -> list[dict]:
    """Your men the market priced lowest relative to what the model expects.

    Not "who is good" -- that is the roster. This is where YOUR team disagrees
    with the room, which is the only part of a draft that can actually beat it.
    Ranked by value over what a player at that ADP normally returns.
    """
    if not my_ids:
        return []
    from fantasyedge.trade import market

    # FIT ON THE WHOLE BOARD, then look at yours. The curve is "what a player
    # at this ADP normally returns", which needs the whole market to describe;
    # fitting it on the five men you drafted asks what a player at this ADP
    # returns among your own picks, and answers nothing.
    priced = (market.value_curve(board)
              .filter(pl.col("player_id").is_in(my_ids)
                      # Same reason they are off the draft card: a kicker
                      # "underpriced by 4" is the noise in deep ADP, and it
                      # pushes a real name off a five-row list.
                      & ~pl.col("position").is_in(list(NOT_WORTH_FIXING))))
    if "market_edge" not in priced.columns:
        return []
    return (priced.filter(pl.col("market_edge") > 0)
                  .sort("market_edge", descending=True)
                  .head(top)
                  .select([c for c in ("player_id", "player_name", "position",
                                       "ecr", "vor", "market_value",
                                       "market_edge", "season_p20",
                                       "season_p80")
                           if c in priced.columns])
                  .to_dicts())
