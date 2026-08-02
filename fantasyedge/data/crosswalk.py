"""Player ID reconciliation — the spine everything else joins through.

Fantasy data lives across five ID systems that agree on nothing:

    nflverse (gsis_id)  ->  player_stats, pbp, snap_counts, injuries
    espn_id             ->  the live league via espn-api
    fantasypros_id      ->  ADP / expert consensus rankings
    sleeper_id          ->  Sleeper league data
    pfr_id              ->  snap counts and advanced stats

Joining on player *names* looks like it works and then quietly breaks on
suffixes (Jr./III), apostrophes, nicknames, and D/ST naming. ffverse already
solved this; `load_ff_playerids()` is that solution. Build the crosswalk
first and route every downstream join through it.
"""

from __future__ import annotations

import re

import polars as pl

from fantasyedge import config
from fantasyedge.data import pull

ID_COLUMNS = [
    "gsis_id", "espn_id", "sleeper_id", "fantasypros_id",
    "pfr_id", "yahoo_id", "mfl_id",
]

BIO_COLUMNS = [
    "name", "merge_name", "position", "team", "birthdate", "age",
    "draft_year", "draft_round", "draft_pick", "draft_ovr",
    "height", "weight", "college",
]


def build() -> pl.DataFrame:
    """The crosswalk, restricted to modelled positions with a usable gsis_id."""
    ids = pull.load("ff_playerids")

    keep = [c for c in ID_COLUMNS + BIO_COLUMNS if c in ids.columns]
    df = ids.select(keep)

    # A row with no gsis_id can't be joined to nflverse — it's dead weight here.
    df = df.filter(pl.col("gsis_id").is_not_null())

    df = df.filter(pl.col("position").is_in(list(config.MODELED_POSITIONS)))

    # One row per gsis_id. Duplicates exist where a player changed teams; the
    # crosswalk is identity-only, so team is not what we're keying on.
    df = df.unique(subset=["gsis_id"], keep="first")

    return df


def normalize_name(name: str) -> str:
    """Last-resort name key for sources with no ID at all.

    Use this only when a source genuinely has no ID column. Any join that
    falls back to names should be counted and reported, not trusted silently.
    """
    n = name.lower().strip()
    n = re.sub(r"[.'’,]", "", n)
    n = re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", n)
    n = re.sub(r"\s+", " ", n)
    return n


def coverage_report(df: pl.DataFrame) -> pl.DataFrame:
    """How complete is each ID system? Drives what we can actually join."""
    rows = []
    for col in ID_COLUMNS:
        if col not in df.columns:
            continue
        present = df.filter(pl.col(col).is_not_null()).height
        rows.append({
            "id_system": col,
            "present": present,
            "missing": df.height - present,
            "pct": round(100 * present / df.height, 1) if df.height else 0.0,
        })
    return pl.DataFrame(rows).sort("pct", descending=True)


def audit_join(
    left: pl.DataFrame,
    crosswalk: pl.DataFrame,
    left_on: str,
    right_on: str = "gsis_id",
    label: str = "join",
) -> tuple[pl.DataFrame, dict]:
    """Join through the crosswalk and report what failed to match.

    A silent left join hides broken keys. This returns the match rate alongside
    the result so an unnoticed 60%-match join can't slip into a feature table.
    """
    before = left.height
    joined = left.join(crosswalk, left_on=left_on, right_on=right_on, how="left")

    check_col = "name" if "name" in crosswalk.columns else right_on
    matched = joined.filter(pl.col(check_col).is_not_null()).height

    stats = {
        "label": label,
        "rows": before,
        "matched": matched,
        "unmatched": before - matched,
        "match_rate": round(100 * matched / before, 2) if before else 0.0,
    }
    return joined, stats


def save(df: pl.DataFrame) -> None:
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    df.write_parquet(config.PROCESSED / "crosswalk.parquet")


def load() -> pl.DataFrame:
    path = config.PROCESSED / "crosswalk.parquet"
    if not path.exists():
        raise FileNotFoundError("crosswalk not built; run scripts/pull_data.py")
    return pl.read_parquet(path)
