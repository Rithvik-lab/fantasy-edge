#!/usr/bin/env python
"""Phase 1: the two tests that decide whether the thesis holds.

  1. value_gap_hit_rate  -- when model and market disagreed, who was right?
  2. quantile_calibration -- is the predicted floor actually a floor?

Neither needs new modelling. Both use validation seasons only; the checkpoint
seasons are not touched here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

from fantasyedge import config  # noqa: E402
from fantasyedge.data import market  # noqa: E402
from fantasyedge.diagnostics import report as R  # noqa: E402
from fantasyedge.eval import scorecard  # noqa: E402
from fantasyedge.features import build as fb  # noqa: E402
from fantasyedge.models import train as T  # noqa: E402

RULE = "=" * 70


def main() -> int:
    config.assert_no_leakage()
    table = fb.load()

    print(f"{RULE}\nTRAINING MODELS (walk-forward, validation seasons only)\n{RULE}")
    res = T.train_all(table)

    ppg = res["ppg"]["oof"].rename({"pred": "ppg_pred"})
    gms = res["games"]["oof"].select(
        ["player_id", "season", "pred"]
    ).rename({"pred": "games_pred"})

    proj = (
        ppg.join(gms, on=["player_id", "season"], how="inner")
        .with_columns((pl.col("ppg_pred") * pl.col("games_pred")).alias("model_points"))
    )

    actual = table.select(["player_id", "season", "total_points"])
    proj = proj.join(actual, on=["player_id", "season"], how="inner")

    # ------------------------------------------------------------------
    # 1. Does the model beat the market at picking?
    # ------------------------------------------------------------------
    print(f"\n{RULE}\n1. VALUE GAP HIT RATE — model vs market\n{RULE}")

    val_seasons = [s for _, s in config.VALIDATION_FOLDS]
    hist = market.historical(val_seasons)
    priced_seasons = sorted(hist["season"].unique().to_list())
    print(f"seasons with a contemporaneous price: {priced_seasons}")
    print(f"(the ranking archive starts late 2019, so earlier folds have none)\n")

    df = proj.join(
        hist.select(["player_id", "season", "ecr", "market_name"]),
        on=["player_id", "season"],
        how="inner",
    )

    # Ranks are lower-is-better and computed within season so they are
    # comparable to ECR.
    df = df.with_columns([
        pl.col("model_points").rank("ordinal", descending=True).over("season")
          .alias("model_rank"),
        pl.col("ecr").rank("ordinal").over("season").alias("market_rank"),
        pl.col("total_points").rank("ordinal", descending=True).over("season")
          .alias("actual_finish"),
    ]).with_columns(
        (pl.col("market_rank") - pl.col("model_rank")).alias("value_gap")
    )

    print(f"players with both a model projection and a market price: {df.height:,}\n")

    results = {}
    for thr in (12, 24, 36):
        hr = R.value_gap_hit_rate(df, threshold=thr)
        results[thr] = hr
        if "error" in hr:
            print(f"  threshold {thr:>3}: {hr['error']}")
        else:
            print(f"  threshold {thr:>3}:  {hr['calls_made']:>4} calls   "
                  f"{hr['hit_rate']:>5.1f}% correct")

    print("\n  50% = no edge over ADP.")

    # Does the model at least separate outcomes at all?
    corr_model = df.select(pl.corr("model_rank", "actual_finish")).item()
    corr_market = df.select(pl.corr("market_rank", "actual_finish")).item()
    print(f"\n  rank correlation with actual finish:")
    print(f"    model  {corr_model:+.4f}")
    print(f"    market {corr_market:+.4f}   <- the bar")

    # ------------------------------------------------------------------
    # 2. Are the quantiles calibrated?
    # ------------------------------------------------------------------
    print(f"\n{RULE}\n2. QUANTILE CALIBRATION — is the floor a floor?\n{RULE}")

    q = None
    for name in ("q20", "q50", "q80"):
        oof = res[name]["oof"].select(
            ["player_id", "season", "pred"]
        ).rename({"pred": name})
        q = oof if q is None else q.join(oof, on=["player_id", "season"], how="inner")

    weekly_actual = table.select(
        ["player_id", "season", "weekly_q50"]
    ).rename({"weekly_q50": "actual"})
    q = q.join(weekly_actual, on=["player_id", "season"], how="inner")

    print(f"rows: {q.height:,}\n")
    print(R.quantile_calibration(q))
    print("\n  'empirical_coverage' should track 'expected_coverage'.")
    print("  A q20 that only contains 8% of outcomes is not a floor.")

    # ------------------------------------------------------------------
    # Log it
    # ------------------------------------------------------------------
    maes = [m["mae"] for m in res["ppg"]["folds"].values()]
    hr24 = results.get(24, {})
    scorecard.log_run(
        name="phase1_baseline",
        label="ppg",
        features=T.feature_columns(table.filter(pl.col("prior_games") >= 4)),
        params=dict(config.XGB_DEFAULTS),
        validation={
            "mae": round(float(np.mean(maes)), 4),
            "mae_sd": round(float(np.std(maes)), 4),
            "baseline_mae": res["ppg"]["baseline_mae"],
            "hit_rate_24": hr24.get("hit_rate"),
            "rank_corr_model": round(corr_model, 4),
            "rank_corr_market": round(corr_market, 4),
        },
        notes="Phase 1 baseline: 28 features, no QB rushing, no vacated targets.",
    )
    print(f"\n{RULE}\nlogged to {scorecard.LOG.name}\n{RULE}")
    print(scorecard.progress("ppg"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
