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

# Season totals conflate how good a rookie was with how available he was, and
# for a first-year player those are very different questions. Omarion Hampton
# scored 15.08 per game in 2025 -- second among rookie backs, ahead of Jeanty
# -- and finished 5th by total because he played nine games. Ranking him on
# the total says "bust"; ranking him on the rate says "the best back in the
# class, hurt".
#
# So the rookie model decomposes exactly as the veteran model does:
#
#     season points  =  points per game  x  games played
#
# Rate is the talent estimate and carries forward to year two. Games is
# mostly luck for a rookie and should not contaminate the talent signal.
LABEL = "rookie_ppg"
LABEL_TOTAL = "rookie_points"
LABEL_GAMES = "rookie_games"

# A rookie who played twice tells you almost nothing per-game; the rate is
# noise at that sample size.
MIN_GAMES_FOR_RATE = 4

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


def baseline_table(train: pl.DataFrame, label: str = LABEL) -> pl.DataFrame:
    """Historical mean by position and round -- the bar to clear."""
    return (
        train.group_by(["position", "round"])
        .agg(pl.col(label).mean().alias("baseline_pred"))
    )


def walk_forward(
    df: pl.DataFrame,
    val_seasons: list[int] | None = None,
    min_train: int = 300,
    label: str = LABEL,
) -> tuple[pl.DataFrame, dict]:
    """Project each draft class using only earlier classes."""
    hist = df.filter(pl.col("season") <= config.RAW_SEASON_END)
    if label == LABEL:   # rate needs a minimum sample to be meaningful
        hist = hist.filter(pl.col(LABEL_GAMES) >= MIN_GAMES_FOR_RATE)
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
        model.fit(_matrix(tr, feats), tr[label].to_numpy(), verbose=False)

        base = baseline_table(tr, label)
        va = va.join(base, on=["position", "round"], how="left").with_columns(
            pl.col("baseline_pred").fill_null(tr[label].mean())
        )

        oof.append(va.select([
            "gsis_id", "player_name", "position", "season", "round", "pick",
            "pos_draft_rank", "baseline_pred", LABEL_GAMES,
            pl.col(label).alias("actual"),
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
    """Project an incoming draft class as rate x availability.

    Two models rather than one. `projected_ppg` is the talent estimate and is
    the number to read when comparing prospects; `projected_points` is what a
    fantasy season is actually worth and folds in expected availability.

    Ranking on the total alone is what makes an injured standout look like a
    bust -- it is the same conflation the veteran model avoids.
    """
    hist = df.filter(pl.col("season") <= config.RAW_SEASON_END)
    feats = feature_list(hist)

    rate_train = hist.filter(pl.col(LABEL_GAMES) >= MIN_GAMES_FOR_RATE)
    rate = xgb.XGBRegressor(**PARAMS)
    rate.fit(_matrix(rate_train, feats), rate_train[LABEL].to_numpy(), verbose=False)

    games = xgb.XGBRegressor(**PARAMS)
    games.fit(_matrix(hist, feats), hist[LABEL_GAMES].to_numpy(), verbose=False)

    cls = df.filter(pl.col("season") == season)
    if not cls.height:
        return pl.DataFrame()

    X = _matrix(cls, feats)
    ppg = rate.predict(X)
    gms = np.clip(games.predict(X), 0, 17)

    return (
        cls.select(["gsis_id", "player_name", "position", "team", "round",
                    "pick", "pos_draft_rank", "college"])
        .with_columns([
            pl.Series("projected_ppg", ppg),
            pl.Series("projected_games", gms),
            pl.Series("projected_points", ppg * gms),
        ])
        .with_columns([
            pl.col("projected_points").rank("ordinal", descending=True)
            .over("position").cast(pl.Int32).alias("proj_pos_rank"),
            pl.col("projected_ppg").rank("ordinal", descending=True)
            .over("position").cast(pl.Int32).alias("talent_pos_rank"),
        ])
        .sort("projected_points", descending=True)
    )
