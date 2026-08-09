"""Keeping the season current.

WHAT WAS ACTUALLY SYNCED BEFORE THIS

Draft day, and only draft day. `espn_draft` polls the live draft every couple
of seconds and that part works. Everything else was a manual script run
whenever someone remembered: `weekly_features.parquet` was built once by hand,
depth charts were never pulled at all, and the in-season model had no way to
learn that week 3 had happened.

Two clocks, deliberately kept apart:

    ROSTERS     one cheap call to ESPN. Cheap enough that there is no reason
                to ever be stale -- refreshed on open and on demand.
    THE SEASON  stats, depth charts, injuries. New evidence only exists after
                games settle, so this runs weekly. Polling it hourly would
                re-read the same numbers and pretend that was an update.

TOLERATING A SEASON THAT HAS NOT STARTED

Probed on 2026-08-04, before week one:

    depth_charts   400,785 rows   live, republished daily
    schedules          272 rows   full season published
    player_stats            none  no file yet
    injuries                none  no file yet
    snap_counts             none  no file yet

A static coverage table cannot say that, which is why the one in config caps
everything at 2025 and would silently skip the current season. So this asks,
records what it actually got, and degrades: a missing dataset is a normal
August condition, not an error. The stamp says what is fresh and what is not,
so callers can tell "no injuries reported" from "we never looked".
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import polars as pl

from fantasyedge import config

# nflreadpy caches in MEMORY by default, which means nothing survives the
# process. The scheduler runs in a fresh process every time, so every pull was
# re-downloading roughly 2 MB it already had. Filesystem cache with a one-day
# life makes a repeat pull free and is politer to a volunteer-run project that
# serves this data for nothing. Set before nflreadpy is imported anywhere.
os.environ.setdefault("NFLREADPY_CACHE", "filesystem")
os.environ.setdefault("NFLREADPY_CACHE_DURATION", "86400")

STAMP = config.PROCESSED / "refresh.json"

# Weekly, after games settle. Tuesday is the earliest every result is final,
# including the Monday night game.
SEASON_MAX_AGE = 6 * 24 * 3600

# A pull that came back with nothing is NOT a fresh week of data, and must not
# buy a week of silence. Week one 2026 kicks off 9 September; a pull on the 8th
# gets a 404, and under the weekly rule the next attempt would be the 14th --
# so the model would sit five days behind the opening Sunday having decided it
# was up to date. An empty result retries in hours instead.
EMPTY_RETRY_AGE = 6 * 3600
# A roster is one HTTP call, so "stale" here means minutes, not days.
ROSTER_MAX_AGE = 15 * 60


@dataclass
class Result:
    name: str
    rows: int = 0
    ok: bool = False
    note: str = ""


@dataclass
class Stamp:
    season: int = 0
    at: float = 0.0
    week: int | None = None
    results: list[dict] = field(default_factory=list)

    @property
    def age(self) -> float:
        return time.time() - self.at if self.at else float("inf")

    @property
    def got_stats(self) -> bool:
        """Did the pull actually bring back game results, or only the chart?

        Depth charts publish year round, so "we got something" is not the same
        as "the season has started" -- and treating it that way is what let an
        empty August pull look fresh.
        """
        return any(r.get("ok") and r.get("name") == "player_stats"
                   for r in self.results)

    @property
    def stale(self) -> bool:
        return self.age > (SEASON_MAX_AGE if self.got_stats else EMPTY_RETRY_AGE)


def read_stamp() -> Stamp:
    if not STAMP.exists():
        return Stamp()
    try:
        return Stamp(**json.loads(STAMP.read_text()))
    except Exception:
        return Stamp()


def _write_stamp(s: Stamp) -> None:
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(json.dumps(asdict(s), indent=2))


def _out(name: str, season: int) -> Path:
    return config.PROCESSED / f"{name}_{season}.parquet"


# Only what the in-season model and trade engine actually read. Pulling the
# whole nflverse catalogue weekly would be minutes of downloads to feed
# features nothing consumes.
def _sources() -> dict:
    import nflreadpy as nfl
    return {
        "player_stats": lambda s: nfl.load_player_stats(seasons=[s]),
        "depth_charts": lambda s: nfl.load_depth_charts(seasons=[s]),
        "injuries": lambda s: nfl.load_injuries(seasons=[s]),
        "snap_counts": lambda s: nfl.load_snap_counts(seasons=[s]),
        # Kickers live in player_stats already, but a DEFENCE is a team and has
        # no row there -- without team_stats the position simply does not exist
        # week to week. Schedules carry the spread and total, which is what the
        # whole streaming model runs on, and they publish months ahead.
        "team_stats": lambda s: nfl.load_team_stats(seasons=[s]),
        "schedules": lambda s: nfl.load_schedules(seasons=[s]),
        # The rest of CORE, per config.FEATURE_TIERS. These are what the
        # feature builder reads, so leaving them out meant the weekly pull kept
        # the model fed on results while starving it of everything that
        # explains them.
        #
        #   ff_opportunity  expected points from usage, which is the whole
        #                   basis of the opportunity/efficiency split
        #   pbp             per-play detail: roof and surface as PLAYED rather
        #                   than as scheduled, air yards, red-zone work
        #   rosters_weekly  who was on which team that week -- a player traded
        #                   mid-season is otherwise attributed to the wrong one
        #
        # Measured before adding: 0.7s / 0.3s / 1.2s and a few tens of MB.
        # Cheap enough that leaving them out was never the saving it looked
        # like. Stadium geometry needs no pull at all -- roof, surface,
        # altitude and cold-weather flags are a static table in data/stadiums,
        # and weather is derived from that plus the schedule.
        "ff_opportunity": lambda s: nfl.load_ff_opportunity(
            seasons=[s], stat_type="weekly"),
        "rosters_weekly": lambda s: nfl.load_rosters_weekly(seasons=[s]),
        "pbp": lambda s: nfl.load_pbp(seasons=[s]),
    }


def season(target: int | None = None, force: bool = False) -> Stamp:
    """Pull this season's stats, depth charts and injuries.

    Safe to call often -- it returns the existing stamp untouched if the last
    pull is still inside the weekly window.
    """
    target = target or config.PRODUCTION_TARGET_SEASON
    prev = read_stamp()
    if not force and prev.season == target and not prev.stale:
        return prev

    results: list[Result] = []
    week = None
    for name, fn in _sources().items():
        try:
            d = fn(target)
        except Exception as e:
            # August, most likely: the file for this season does not exist yet.
            results.append(Result(name, note=f"not published ({type(e).__name__})"))
            continue
        if d is None or not d.height:
            results.append(Result(name, note="empty"))
            continue
        d.write_parquet(_out(name, target))
        if "week" in d.columns:
            try:
                w = int(d["week"].max())
                week = w if week is None else max(week, w)
            except Exception:
                pass
        results.append(Result(name, rows=d.height, ok=True))

    s = Stamp(season=target, at=time.time(), week=week,
              results=[asdict(r) for r in results])
    _write_stamp(s)
    return s


def observed(target: int | None = None, through_week: int | None = None) -> pl.DataFrame:
    """Season-to-date per-game scoring, in the shape `inseason.reprice` wants.

    Reads the refreshed pull rather than the historical feature table, so it
    reflects games that have happened since features were last built.
    """
    from fantasyedge.models import inseason

    target = target or config.PRODUCTION_TARGET_SEASON
    p = _out("player_stats", target)
    if not p.exists():
        return pl.DataFrame()

    d = pl.read_parquet(p)
    id_col = next((c for c in ("player_id", "gsis_id") if c in d.columns), None)
    pts_col = next((c for c in ("fantasy_points_ppr", "fantasy_points")
                    if c in d.columns), None)
    if not id_col or not pts_col:
        return pl.DataFrame()

    d = d.rename({id_col: "player_id", pts_col: "fantasy_points_ppr"})
    if through_week is None:
        through_week = int(d["week"].max()) if "week" in d.columns else 0
    if "season" not in d.columns:
        d = d.with_columns(pl.lit(target).alias("season"))

    return inseason.observe(d, target, through_week)


def kickoff(target: int | None = None) -> str | None:
    """First game of the season, as a date string. Free -- schedules publish early.

    Worth surfacing: before this date every stats file is a 404, and an app
    that only says "not published" reads as broken rather than as early.
    """
    target = target or config.PRODUCTION_TARGET_SEASON
    try:
        import nflreadpy as nfl

        s = nfl.load_schedules(seasons=[target])
        if not s.height or "gameday" not in s.columns:
            return None
        return str(s.filter(pl.col("week") == 1)["gameday"].min())
    except Exception:
        return None


def describe() -> dict:
    """What is fresh, for the status line. Distinguishes stale from never."""
    s = read_stamp()
    if not s.at:
        return {"synced": False, "note": "the season has never been pulled"}
    got = [r["name"] for r in s.results if r.get("ok")]
    missing = [r["name"] for r in s.results if not r.get("ok")]
    out = {
        "synced": True,
        "season": s.season,
        "week": s.week,
        "age_hours": round(s.age / 3600, 1),
        "stale": s.stale,
        "have": got,
        "not_published": missing,
        "has_results": s.got_stats,
    }
    if not s.got_stats:
        out["kickoff"] = kickoff(s.season)
        out["note"] = (
            "No games have been played yet, so there are no weekly results to "
            "pull. Depth charts and the schedule are already live."
        )
    return out


def stream_inputs(target: int | None = None,
                  fit_seasons: int = 4) -> dict:
    """Everything the K/DST streaming model needs, from the weekly pull.

    Returns the fitted residual table per opponent, plus the per-team implied
    points for each remaining game. All of it derived rather than frozen in
    source, because rosters turn over and a hardcoded list of thirty-two teams
    rots quietly.

    THE FIT AND THE FIXTURE LIST ARE ON DIFFERENT CLOCKS. Which games are
    coming is a question about THIS season and comes from the weekly pull. How
    soft an opponent is takes several years to see -- one season gives about
    seventeen games a team, and the split-half test says that is too few to
    separate a real edge from noise. So the residuals are fitted over the last
    `fit_seasons` years, read straight from nflverse (cached), and the current
    season supplies only the schedule.
    """
    from fantasyedge.models import kdst

    target = target or config.PRODUCTION_TARGET_SEASON
    sp, tp, kp = (_out("schedules", target), _out("team_stats", target),
                  _out("player_stats", target))
    # The fit does NOT depend on this season's schedule -- it is history. An
    # early return here took the edges down with the fixture list, so in August,
    # before any 2026 line is published, the whole model came back empty when
    # only half of it was actually unavailable.
    if not sp.exists():
        return {"edges": _fit_edges(target, fit_seasons), "games": pl.DataFrame()}

    sch = pl.read_parquet(sp)
    need = {"week", "home_team", "away_team", "spread_line", "total_line"}
    if not need <= set(sch.columns):
        return {"edges": _fit_edges(target, fit_seasons), "games": pl.DataFrame()}

    def side(t: str, o: str, sign: int) -> pl.DataFrame:
        cols = ["week", pl.col(t).alias("team"), pl.col(o).alias("opp"),
                (pl.col("total_line") / 2
                 + sign * pl.col("spread_line") / 2).alias("implied_for"),
                (pl.col("total_line") / 2
                 - sign * pl.col("spread_line") / 2).alias("implied_against")]
        if "home_score" in sch.columns:
            cols.append(pl.col("away_score" if sign > 0 else "home_score")
                        .alias("pts_allowed"))
        return sch.select(cols)

    games = pl.concat([side("home_team", "away_team", 1),
                       side("away_team", "home_team", -1)]).drop_nulls(
        ["implied_for", "implied_against"])

    return {"edges": _fit_edges(target, fit_seasons), "games": games}


def _fit_edges(target: int, back: int) -> dict[str, dict[str, float]]:
    """Per-opponent residuals over the last few completed seasons."""
    from fantasyedge.models import kdst

    try:
        import nflreadpy as nfl

        years = [y for y in range(target - back, target) if y >= 2002]
        if not years:
            return {"K": {}, "DST": {}}
        sch = nfl.load_schedules(seasons=years)
        keep = ["season", "week", "home_team", "away_team", "spread_line",
                "total_line", "home_score", "away_score"]
        if not set(keep) <= set(sch.columns):
            return {"K": {}, "DST": {}}
        sch = sch.select(keep).drop_nulls()

        def side(t, o, sign, sc):
            return sch.select([
                "season", "week", pl.col(t).alias("team"), pl.col(o).alias("opp"),
                (pl.col("total_line") / 2
                 + sign * pl.col("spread_line") / 2).alias("implied_for"),
                (pl.col("total_line") / 2
                 - sign * pl.col("spread_line") / 2).alias("implied_against"),
                pl.col(sc).alias("pts_allowed")])

        g = pl.concat([side("home_team", "away_team", 1, "away_score"),
                       side("away_team", "home_team", -1, "home_score")])

        ps = nfl.load_player_stats(seasons=years)
        kicks = None
        if {"position", "fg_made", "pat_made"} <= set(ps.columns):
            kicks = (ps.filter(pl.col("position") == "K")
                       .select(["season", "week", "team", "fg_made", "pat_made"])
                       .drop_nulls()
                       .with_columns((pl.col("fg_made") * 3
                                      + pl.col("pat_made")).alias("pts"))
                       .join(g, on=["season", "week", "team"], how="inner")
                       .with_columns((kdst.K_INTERCEPT + kdst.K_SLOPE
                                      * pl.col("implied_for")).alias("expected")))

        ts = nfl.load_team_stats(seasons=years)
        cnt = pl.lit(0.0)
        for c, w in (("def_sacks", 1), ("def_interceptions", 2),
                     ("def_fumble_recovery_opp", 2), ("def_tds", 6),
                     ("def_safeties", 2)):
            if c in ts.columns:
                cnt = cnt + pl.col(c).fill_null(0) * w
        tier = (pl.when(pl.col("pts_allowed") == 0).then(10)
                .when(pl.col("pts_allowed") <= 6).then(7)
                .when(pl.col("pts_allowed") <= 13).then(4)
                .when(pl.col("pts_allowed") <= 20).then(1)
                .when(pl.col("pts_allowed") <= 27).then(0)
                .when(pl.col("pts_allowed") <= 34).then(-1).otherwise(-4))
        defs = (ts.with_columns(cnt.alias("_c"))
                  .select(["season", "week", "team", "_c"])
                  .join(g, on=["season", "week", "team"], how="inner")
                  .with_columns([(pl.col("_c") + tier).alias("pts"),
                                 (kdst.DST_INTERCEPT + kdst.DST_SLOPE
                                  * pl.col("implied_against")).alias("expected")]))
        return kdst.opponent_edge(kicks, defs)
    except Exception:
        # No fit is a fine answer: stream_score falls back to the line, which
        # is where nearly all of the signal lives anyway.
        return {"K": {}, "DST": {}}
