#!/usr/bin/env python
"""Build the player-season feature table and report what landed.

    python scripts/build_features.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl  # noqa: E402

from fantasyedge import config  # noqa: E402
from fantasyedge.features import build as fb  # noqa: E402


def main() -> int:
    config.assert_no_leakage()

    print("Building feature table...\n")
    table = fb.build()
    fb.save(table)

    train = fb.training_frame(table)

    print(f"rows                 {table.height:,}")
    print(f"columns              {table.width}")
    print(f"seasons              {table['season'].min()}-{table['season'].max()}")
    print(f"usable for training  {train.height:,}  (prior_games >= 4)")

    print("\nrows per split")
    for name, seasons in (
        ("train  2014-2022", config.TRAIN_SEASONS),
        ("test   2024", [2024]),
        ("test   2025", [2025]),
        ("predict 2026", [config.PRODUCTION_TARGET_SEASON]),
    ):
        n = table.filter(pl.col("season").is_in(seasons)).height
        print(f"  {name:<20} {n:>6,}")

    print("\nrows by position (training window)")
    print(
        train.filter(pl.col("season").is_in(config.TRAIN_SEASONS))
        .group_by("position")
        .agg([
            pl.len().alias("rows"),
            pl.col("total_points").mean().round(1).alias("mean_points"),
            pl.col("weekly_sd").mean().round(2).alias("mean_weekly_sd"),
        ])
        .sort("rows", descending=True)
    )

    print("\nfeature null rates (training window, worst 12)")
    tw = train.filter(pl.col("season").is_in(config.TRAIN_SEASONS))
    feats = [c for c in tw.columns if c.startswith("prior_")]
    nulls = pl.DataFrame({
        "feature": feats,
        "null_pct": [round(100 * tw[c].null_count() / tw.height, 1) for c in feats],
    }).sort("null_pct", descending=True)
    print(nulls.head(12))

    print(f"\nsaved -> {config.PROCESSED / 'features.parquet'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
