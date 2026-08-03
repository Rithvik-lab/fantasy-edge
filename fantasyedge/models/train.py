"""Train the expected-points, availability, and quantile models.

Season value decomposes rather than being predicted in one shot:

    season points  =  points per game  x  games played

Predicting the product directly conflates a good player who got hurt with a
mediocre one who stayed healthy. Those are different problems with different
features, so they get different models.

The quantile models predict weekly floor / median / ceiling directly, which
is what the risk pillar needs -- and unlike a predicted standard deviation,
they carry no normality assumption. Fantasy scoring is right-skewed because
touchdowns arrive in lumps.

Everything is evaluated by walk-forward validation. The sealed test seasons
are never touched here.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import xgboost as xgb

from fantasyedge import config
from fantasyedge.features import build as fb

# Anything the model may look at. Deliberately excludes ecr -- feeding the
# market price in would just teach the model to reproduce it, and the whole
# point is to disagree with it usefully.
EXCLUDE = {
    "player_id", "player_name", "position", "team", "season",
    "total_points", "ppg", "games", "weekly_sd",
    "weekly_q20", "weekly_q50", "weekly_q80",
    "ecr", "sd", "vor",
}


def feature_columns(df: pl.DataFrame) -> list[str]:
    return [
        c for c in df.columns
        if c not in EXCLUDE and df[c].dtype in (
            pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.UInt32, pl.Boolean
        )
    ]


def _matrix(df: pl.DataFrame, feats: list[str]) -> np.ndarray:
    return df.select(feats).to_pandas().astype("float32").values


def fit(
    train: pl.DataFrame,
    feats: list[str],
    label: str,
    objective: str = "reg:squarederror",
    quantile: float | None = None,
    early_stop: pl.DataFrame | None = None,
    **overrides,
) -> xgb.XGBRegressor:
    """Fit one model.

    `early_stop` should be a held-out slice of the *training* window, never a
    validation or test season. Without it, 1000 trees on ~3000 rows memorises
    the training set and underperforms a naive last-season carry-forward.
    """
    params = dict(config.XGB_DEFAULTS)
    params.update(overrides)
    params["objective"] = objective
    if quantile is not None:
        params["quantile_alpha"] = quantile
    if early_stop is not None and early_stop.height:
        params["early_stopping_rounds"] = 50

    model = xgb.XGBRegressor(**params)
    kw = {}
    if early_stop is not None and early_stop.height:
        kw["eval_set"] = [(_matrix(early_stop, feats),
                           early_stop[label].to_numpy())]
    model.fit(_matrix(train, feats), train[label].to_numpy(), verbose=False, **kw)
    return model


def walk_forward(
    table: pl.DataFrame,
    label: str,
    objective: str = "reg:squarederror",
    quantile: float | None = None,
    min_prior_games: int = 4,
) -> tuple[pl.DataFrame, dict[int, dict]]:
    """Train and predict across every validation fold.

    Returns out-of-fold predictions (each row predicted by a model that never
    saw its season) plus per-fold metrics.
    """
    usable = table.filter(
        pl.col("prior_games").ge(min_prior_games) & pl.col(label).is_not_null()
    )
    feats = feature_columns(usable)

    oof, metrics = [], {}
    for train_seasons, val_season in config.VALIDATION_FOLDS:
        va = usable.filter(pl.col("season") == val_season)
        if not va.height:
            continue

        # Hold out the most recent training season to stop on. It must come
        # from inside the training window -- stopping on the validation
        # season would leak it into every hyperparameter decision.
        stop_season = max(train_seasons)
        tr = usable.filter(
            pl.col("season").is_in([s for s in train_seasons if s != stop_season])
        )
        es = usable.filter(pl.col("season") == stop_season)
        if not tr.height:
            tr, es = usable.filter(pl.col("season").is_in(train_seasons)), None

        model = fit(tr, feats, label, objective, quantile, early_stop=es)
        pred = model.predict(_matrix(va, feats))

        oof.append(va.select([
            "player_id", "player_name", "position", "season",
            pl.col(label).alias("actual"),
        ]).with_columns(pl.Series("pred", pred)))

        err = np.abs(va[label].to_numpy() - pred)
        metrics[val_season] = {
            "n": va.height,
            "mae": round(float(err.mean()), 3),
            "train_rows": tr.height,
        }

    return (pl.concat(oof) if oof else pl.DataFrame()), metrics


def baseline_mae(table: pl.DataFrame, label: str, prior: str) -> float:
    """Naive baseline: predict this season = last season.

    The model has to beat this. A model that can't is an expensive way to
    copy a column.
    """
    d = table.filter(pl.col(label).is_not_null() & pl.col(prior).is_not_null())
    d = d.filter(pl.col("season").is_in([s for _, s in config.VALIDATION_FOLDS]))
    return round(float((d[label] - d[prior]).abs().mean()), 3)


def train_all(table: pl.DataFrame | None = None) -> dict:
    """Fit every model, returning out-of-fold predictions and metrics."""
    if table is None:
        table = fb.load()

    results: dict = {}

    # rate and availability, the two halves of season value
    for name, label, prior in (
        ("ppg", "ppg", "prior_ppg"),
        ("games", "games", "prior_games"),
        ("total_points", "total_points", "prior_total_points"),
    ):
        oof, metrics = walk_forward(table, label)
        results[name] = {
            "oof": oof,
            "folds": metrics,
            "baseline_mae": baseline_mae(table, label, prior),
        }

    # weekly floor / median / ceiling
    for q in config.QUANTILES:
        key = f"q{int(q * 100)}"
        oof, metrics = walk_forward(
            table,
            label=f"weekly_{key}",
            objective="reg:quantileerror",
            quantile=q,
        )
        results[key] = {"oof": oof, "folds": metrics}

    return results


def fit_production(table: pl.DataFrame, label: str, **kw) -> xgb.XGBRegressor:
    """Refit on everything, including the sealed seasons.

    This is the model that goes into the MCP server. The walk-forward models
    measure; this one predicts.
    """
    usable = table.filter(
        pl.col("prior_games").ge(4) & pl.col(label).is_not_null()
    )
    return fit(usable, feature_columns(usable), label, **kw)
