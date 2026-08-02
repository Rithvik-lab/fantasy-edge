"""Pull nflverse data once, cache it as parquet, never think about it again.

Every loader is wrapped so a season outside a dataset's coverage is skipped
rather than raising — see config.DATASET_COVERAGE for the empirical bounds.

Cached files carry a snapshot date in a sidecar manifest. nflverse updates
continuously during the season, so a reproducible result needs to name the
snapshot it was built from.
"""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Callable

import nflreadpy as nfl
import polars as pl

from fantasyedge import config

MANIFEST = config.RAW / "manifest.json"

# name -> (loader, kwargs). Keys match config.DATASET_COVERAGE.
DATASETS: dict[str, tuple[Callable, dict]] = {
    "player_stats": (nfl.load_player_stats, {"summary_level": "week"}),
    "schedules": (nfl.load_schedules, {}),
    "rosters": (nfl.load_rosters, {}),
    "depth_charts": (nfl.load_depth_charts, {}),
    "injuries": (nfl.load_injuries, {}),
    "snap_counts": (nfl.load_snap_counts, {}),
    "ff_opportunity": (nfl.load_ff_opportunity, {"stat_type": "weekly"}),
    "nextgen_stats_receiving": (nfl.load_nextgen_stats, {"stat_type": "receiving"}),
    "nextgen_stats_rushing": (nfl.load_nextgen_stats, {"stat_type": "rushing"}),
    "nextgen_stats_passing": (nfl.load_nextgen_stats, {"stat_type": "passing"}),
    "pfr_advstats_rec": (nfl.load_pfr_advstats, {"stat_type": "rec", "summary_level": "week"}),
    "pfr_advstats_rush": (nfl.load_pfr_advstats, {"stat_type": "rush", "summary_level": "week"}),
    "ftn_charting": (nfl.load_ftn_charting, {}),
    "pbp": (nfl.load_pbp, {}),
}

# Season-independent tables — pulled whole, no seasons argument.
STATIC_DATASETS: dict[str, Callable] = {
    "players": nfl.load_players,
    "teams": nfl.load_teams,
    "ff_playerids": nfl.load_ff_playerids,
    "contracts": nfl.load_contracts,
}

# Maps a dataset key to its coverage key (variants share one source).
_COVERAGE_KEY = {
    "nextgen_stats_receiving": "nextgen_stats",
    "nextgen_stats_rushing": "nextgen_stats",
    "nextgen_stats_passing": "nextgen_stats",
    "pfr_advstats_rec": "pfr_advstats",
    "pfr_advstats_rush": "pfr_advstats",
}


def _path(name: str) -> "config.Path":
    return config.RAW / f"{name}.parquet"


def _covered(name: str, seasons: list[int]) -> list[int]:
    key = _COVERAGE_KEY.get(name, name)
    if key not in config.DATASET_COVERAGE:
        return seasons
    return config.seasons_for(key, seasons)


def pull_one(name: str, seasons: list[int], force: bool = False) -> pl.DataFrame | None:
    """Pull and cache a single dataset. Returns None if nothing was covered."""
    out = _path(name)
    if out.exists() and not force:
        return pl.read_parquet(out)

    if name in STATIC_DATASETS:
        df = STATIC_DATASETS[name]()
    else:
        loader, kwargs = DATASETS[name]
        wanted = _covered(name, seasons)
        if not wanted:
            print(f"  {name:26s} skipped (no covered seasons)")
            return None
        df = loader(seasons=wanted, **kwargs)

    config.RAW.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    return df


def pull_all(
    seasons: list[int] | None = None,
    force: bool = False,
    include_pbp: bool = True,
) -> dict[str, int]:
    """Pull everything into data/raw/ and write a snapshot manifest.

    pbp is by far the largest download; set include_pbp=False for a quick pass.
    """
    if seasons is None:
        seasons = list(range(config.RAW_SEASON_START, config.RAW_SEASON_END + 1))

    names = list(STATIC_DATASETS) + [
        n for n in DATASETS if include_pbp or n != "pbp"
    ]

    counts: dict[str, int] = {}
    for name in names:
        started = time.time()
        try:
            df = pull_one(name, seasons, force=force)
        except Exception as exc:  # a single dead source shouldn't kill the pull
            print(f"  {name:26s} FAILED  {type(exc).__name__}: {exc}")
            continue
        if df is None:
            continue
        counts[name] = df.height
        print(f"  {name:26s} rows={df.height:>9,}  cols={df.width:>4}  "
              f"({time.time() - started:5.1f}s)")

    _write_manifest(seasons, counts)
    return counts


def _write_manifest(seasons: list[int], counts: dict[str, int]) -> None:
    MANIFEST.write_text(json.dumps({
        "snapshot_date": date.today().isoformat(),
        "nflreadpy_version": nfl.__version__,
        "seasons": [min(seasons), max(seasons)],
        "row_counts": counts,
    }, indent=2))


def load(name: str) -> pl.DataFrame:
    """Read a cached dataset. Raises if it hasn't been pulled."""
    out = _path(name)
    if not out.exists():
        raise FileNotFoundError(
            f"{name} not cached. Run: python scripts/pull_data.py"
        )
    return pl.read_parquet(out)


def snapshot_info() -> dict:
    """The snapshot the cache was built from — cite this in any result."""
    if not MANIFEST.exists():
        raise FileNotFoundError("no manifest; run scripts/pull_data.py first")
    return json.loads(MANIFEST.read_text())
