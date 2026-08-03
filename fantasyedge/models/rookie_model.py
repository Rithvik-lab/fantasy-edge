"""Rookie projection model.

Predicts rookie-season PPR points from draft capital, athletic testing, and
landing spot. Validated walk-forward by draft class -- a model projecting the
2020 class is trained only on classes that had already played.

THE BASELINE THAT MATTERS

Draft capital alone is a strong predictor, so "position and round average" is
the bar. A model that cannot beat a lookup table of historical means by
position and round is not adding anything, and the lookup table is what most
public rookie rankings effectively are.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import xgboost as xgb

from fantasyedge import config
from fantasyedge.features import rookies as R

LABEL = "rookie_points"

PARAMS = {
    "max_depth": 3,          # ~1,600 rows; shallower than the weekly model
    "learning_rate": 0.05,
    "n_estimators": 600,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 8,
    "random_state": config.RANDOM_SEED,
}


def _matrix(df: pl.DataFrame, feats: list[str]) -> np.ndarray:
    return df.select(feats).to_pandas().astype("float32").values


def feature_list(df: pl.DataFrame) -> list[str]:
    return [f for f in R.FEATURES if f in df.columns]


def baseline_table(train: pl.DataFrame) -> pl.DataFrame:
    """Historical mean by position and round -- the bar to clear."""
    return (
        train.group_by(["position", "round"])
        .agg(pl.col(LABEL).mean().alias("baseline_pred"))
    )


def walk_forward(
    df: pl.DataFrame,
    val_seasons: list[int] | None = None,
    min_train: int = 300,
) -> tuple[pl.DataFrame, dict]:
    """Project each draft class using only earlier classes."""
    hist = df.filter(pl.col("season") <= config.RAW_SEASON_END)
    if val_seasons is None:
        val_seasons = list(range(2014, config.RAW_SEASON_END + 1))

    feats = feature_list(hist)
    oof = []

    for season in val_seasons:
        tr = hist.filter(pl.col("season") < season)
        va = hist.filter(pl.col("season") == season)
        if tr.height < min_train or not va.height:
            continue

        model = xgb.XGBRegressor(**PARAMS)
        model.fit(_matrix(tr, feats), tr[LABEL].to_numpy(), verbose=False)

        base = baseline_table(tr)
        va = va.join(base, on=["position", "round"], how="left").with_columns(
            pl.col("baseline_pred").fill_null(tr[LABEL].mean())
        )

        oof.append(va.select([
            "gsis_id", "player_name", "position", "season", "round", "pick",
            "pos_draft_rank", "baseline_pred",
            pl.col(LABEL).alias("actual"),
        ]).with_columns(
            pl.Series("pred", model.predict(_matrix(va, feats)))
        ))

    if not oof:
        return pl.DataFrame(), {}

    out = pl.concat(oof)
    metrics = {
        "n": out.height,
        "model_mae": round(float((out["actual"] - out["pred"]).abs().mean()), 2),
        "baseline_mae": round(
            float((out["actual"] - out["baseline_pred"]).abs().mean()), 2),
        "model_corr": round(out.select(pl.corr("pred", "actual")).item(), 4),
        "baseline_corr": round(
            out.select(pl.corr("baseline_pred", "actual")).item(), 4),
    }
    metrics["lift_pct"] = round(
        100 * (metrics["baseline_mae"] - metrics["model_mae"])
        / metrics["baseline_mae"], 2)
    return out, metrics


def fit_production(df: pl.DataFrame) -> tuple[xgb.XGBRegressor, list[str]]:
    """Train on every completed class, for projecting the incoming one."""
    hist = df.filter(pl.col("season") <= config.RAW_SEASON_END)
    feats = feature_list(hist)
    model = xgb.XGBRegressor(**PARAMS)
    model.fit(_matrix(hist, feats), hist[LABEL].to_numpy(), verbose=False)
    return model, feats


def project(df: pl.DataFrame, season: int) -> pl.DataFrame:
    """Project an incoming draft class."""
    model, feats = fit_production(df)
    cls = df.filter(pl.col("season") == season)
    if not cls.height:
        return pl.DataFrame()

    pred = model.predict(_matrix(cls, feats))
    return (
        cls.select(["gsis_id", "player_name", "position", "team", "round",
                    "pick", "pos_draft_rank", "college"])
        .with_columns(pl.Series("projected_points", pred))
        .with_columns(
            pl.col("projected_points").rank("ordinal", descending=True)
            .over("position").cast(pl.Int32).alias("proj_pos_rank")
        )
        .sort("projected_points", descending=True)
    )
