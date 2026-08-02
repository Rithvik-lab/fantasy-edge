"""Every modelling decision, encoded as data.

Nothing downstream hardcodes a season. Revise the split here and the whole
pipeline follows.
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
MODELS = ROOT / "models"
REPORTS = ROOT / "reports"

# --------------------------------------------------------------------------
# Season split
#
# Feature year != label year. A row predicting season N is built from season
# N-1 (and earlier) features, so raw data starts earlier than the first label
# year to leave room for multi-year rolling windows.
# --------------------------------------------------------------------------

RAW_SEASON_START = 2010
RAW_SEASON_END = 2025  # 2026 hasn't been played yet

TRAIN_SEASONS = list(range(2014, 2023))  # 2014-2022, label years

# Walk-forward validation. Training window always expands and always precedes
# the validation season; no fold sees its own future.
VALIDATION_FOLDS: list[tuple[list[int], int]] = [
    (list(range(2014, 2018)), 2018),
    (list(range(2014, 2019)), 2019),
    (list(range(2014, 2020)), 2020),  # COVID season — expect an outlier fold
    (list(range(2014, 2021)), 2021),
    (list(range(2014, 2022)), 2022),
    (list(range(2014, 2023)), 2023),
]

# Sealed. Run once each, after the config is locked. Do not tune against these.
TEST_SEASONS = [2024, 2025]

# What the production model projects.
PRODUCTION_TARGET_SEASON = 2026

COVID_SEASON = 2020

# --------------------------------------------------------------------------
# Dataset coverage — empirically probed against nflreadpy 0.1.5, not guessed.
#
# These bounds are why feature tiering exists: a feature cannot exist for a
# training row whose season predates its source dataset.
# --------------------------------------------------------------------------

DATASET_COVERAGE: dict[str, tuple[int, int]] = {
    "pbp": (1999, 2025),
    "player_stats": (1999, 2025),
    "schedules": (1999, 2025),
    "rosters": (1999, 2025),
    "depth_charts": (2010, 2025),
    "injuries": (2010, 2025),
    "ff_opportunity": (2010, 2025),
    "snap_counts": (2013, 2025),  # validator allows 2012 but it returns 0 rows
    "nextgen_stats": (2016, 2025),
    "participation": (2016, 2025),
    "pfr_advstats": (2018, 2025),
    "ftn_charting": (2022, 2025),
}

# Feature tiers. CORE spans the whole training window. GATED features only
# exist from their source's start year — restrict the model variant that uses
# them rather than letting XGBoost's null handling learn "era" instead of
# football. IN_SEASON is week-grain only; too few training seasons to trust.
FEATURE_TIERS: dict[str, list[str]] = {
    "core": ["player_stats", "pbp", "schedules", "snap_counts",
             "injuries", "depth_charts", "ff_opportunity"],
    "gated": ["nextgen_stats", "pfr_advstats", "participation"],
    "in_season": ["ftn_charting"],
}

# --------------------------------------------------------------------------
# League rules — full PPR, ESPN snake draft.
#
# Roster config drives replacement level, which is what makes cross-position
# comparison meaningful. A 15-point WR and a 15-point TE are not equally
# valuable; VOR is what separates them.
# --------------------------------------------------------------------------

LEAGUE_SIZE = 10  # TODO: confirm against the real league
SCORING = "full_ppr"
POINTS_PER_RECEPTION = 1.0

STARTING_LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "DST": 1, "K": 1}
FLEX_ELIGIBLE = ("RB", "WR", "TE")

MODELED_POSITIONS = ("QB", "RB", "WR", "TE")

# --------------------------------------------------------------------------
# Modelling
# --------------------------------------------------------------------------

RANDOM_SEED = 1985

# Small dataset (~2.5-3.5k player-seasons). Shallow trees, heavy regularization.
XGB_DEFAULTS = {
    "max_depth": 4,
    "learning_rate": 0.05,
    "n_estimators": 1000,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "random_state": RANDOM_SEED,
}

# Floor / median / ceiling. Predicting distribution shape directly beats
# predicting a spread parameter and assuming normality — fantasy points are
# right-skewed because touchdowns arrive in lumps.
QUANTILES = (0.20, 0.50, 0.80)

# A season is ~17 games. Volatility computed from a handful of games is noise,
# so a player-season needs this many games to be a valid risk-model row.
MIN_GAMES_FOR_VOLATILITY = 10

# Snap share below this means the player was hurt or benched — those games are
# availability risk, not performance volatility. Excluded from the latter.
NORMAL_SNAP_SHARE_FLOOR = 0.40

# Comps. Cluster within position; a peer group needs enough members for its
# average price to mean anything.
COMPS_NEIGHBORS = 10
TARGET_CLUSTER_SIZE = (15, 30)


def coverage_ok(dataset: str, season: int) -> bool:
    """True if `dataset` has data for `season`."""
    if dataset not in DATASET_COVERAGE:
        raise KeyError(f"unknown dataset {dataset!r}")
    lo, hi = DATASET_COVERAGE[dataset]
    return lo <= season <= hi


def seasons_for(dataset: str, seasons: list[int]) -> list[int]:
    """Filter `seasons` down to those `dataset` actually covers."""
    return [s for s in seasons if coverage_ok(dataset, s)]


def assert_no_leakage() -> None:
    """Guard the split invariants. Called on import of the training entrypoint."""
    for train, val in VALIDATION_FOLDS:
        assert max(train) < val, f"fold trains on {max(train)} but validates {val}"
        assert val not in TEST_SEASONS, f"fold validates on sealed season {val}"
    for s in TEST_SEASONS:
        assert s not in TRAIN_SEASONS, f"sealed season {s} is in TRAIN_SEASONS"
