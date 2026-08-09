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

from fantasyedge import config
from fantasyedge.draft.session import optimal_lineup
from fantasyedge.league import LeagueSettings

# A bye is only a problem when it takes out players you would have STARTED.
# Two backup tight ends sharing week nine is not a story.
BYE_WEEKS = 18


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
        where = p.get("overall")
        market = adp.get(pid)
        if where is None or market is None:
            continue
        # Positive = he was still there later than the room said he would be.
        d = float(market) - float(where)
        edge += d
        rows.append({
            "overall": int(where),
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


def summarise(strength: list[dict], grade: dict) -> dict[str, list[str]]:
    """Two or three sentences each way, from the numbers above."""
    strong = [r for r in strength if r["edge"] >= 12]
    weak = [r for r in strength if r["edge"] <= -12]
    holes = [r for r in strength if r["have"] < r["need"]]

    good, bad = [], []
    for r in strong[:3]:
        good.append(f"{r['position']}: {round(r['edge']):+d} points on the "
                    f"average team, {round(r['percentile'] * 100)}th percentile.")
    for r in reversed(weak[-3:]):
        bad.append(f"{r['position']}: {round(r['edge']):+d} against the average "
                   f"team, {round(r['percentile'] * 100)}th percentile.")
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
