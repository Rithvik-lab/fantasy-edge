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
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import polars as pl

from fantasyedge import config

STAMP = config.PROCESSED / "refresh.json"

# Weekly, after games settle. Tuesday is the earliest every result is final,
# including the Monday night game.
SEASON_MAX_AGE = 6 * 24 * 3600
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
    def stale(self) -> bool:
        return self.age > SEASON_MAX_AGE


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


def describe() -> dict:
    """What is fresh, for the status line. Distinguishes stale from never."""
    s = read_stamp()
    if not s.at:
        return {"synced": False, "note": "the season has never been pulled"}
    got = [r["name"] for r in s.results if r.get("ok")]
    missing = [r["name"] for r in s.results if not r.get("ok")]
    return {
        "synced": True,
        "season": s.season,
        "week": s.week,
        "age_hours": round(s.age / 3600, 1),
        "stale": s.stale,
        "have": got,
        "not_published": missing,
    }
