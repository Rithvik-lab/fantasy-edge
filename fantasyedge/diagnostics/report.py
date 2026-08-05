"""Model diagnostics — find where the model fails, not just how well it scores.

A single MAE tells you almost nothing actionable. "MAE 3.9" doesn't say whether
the model is fine everywhere or excellent on WRs and useless on TEs. Every
function here answers "where does this break?", because that question is what
tells you which feature to add next.

The intended loop:

    1. segment_errors()      -> which slice is worst?
    2. feature_importance()  -> is the model using sensible signals there?
    3. add a feature aimed at that slice
    4. fold_stability()      -> did it help across folds, or just one?

Nothing here touches the sealed test seasons. These run on walk-forward
validation output only.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasyedge import config


# --------------------------------------------------------------------------
# Core error metrics
# --------------------------------------------------------------------------

def _mae(y: np.ndarray, yhat: np.ndarray) -> float:
    return float(np.mean(np.abs(y - yhat)))


def _bias(y: np.ndarray, yhat: np.ndarray) -> float:
    """Mean signed error. Positive = model under-predicts this slice."""
    return float(np.mean(y - yhat))


def _rmse(y: np.ndarray, yhat: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y - yhat) ** 2)))


def overall(df: pl.DataFrame, actual: str = "actual", pred: str = "pred") -> dict:
    y, yhat = df[actual].to_numpy(), df[pred].to_numpy()
    return {
        "n": df.height,
        "mae": round(_mae(y, yhat), 3),
        "rmse": round(_rmse(y, yhat), 3),
        "bias": round(_bias(y, yhat), 3),
        "corr": round(float(np.corrcoef(y, yhat)[0, 1]), 3),
    }


# --------------------------------------------------------------------------
# Where does it break?
# --------------------------------------------------------------------------

def segment_errors(
    df: pl.DataFrame,
    by: str,
    actual: str = "actual",
    pred: str = "pred",
    min_n: int = 20,
) -> pl.DataFrame:
    """Error broken down by a column. The single most useful diagnostic.

    `bias` is the column to read. A slice with large positive bias is one the
    model systematically under-rates — that is a missing feature, not noise.
    """
    rows = []
    for (key,), grp in df.group_by([by], maintain_order=True):
        if grp.height < min_n:
            continue
        y, yhat = grp[actual].to_numpy(), grp[pred].to_numpy()
        rows.append({
            by: key,
            "n": grp.height,
            "mae": round(_mae(y, yhat), 3),
            "bias": round(_bias(y, yhat), 3),
            "mean_actual": round(float(np.mean(y)), 2),
        })
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows).sort("mae", descending=True)


def tier_errors(
    df: pl.DataFrame,
    value_col: str = "ecr",
    n_tiers: int = 5,
    actual: str = "actual",
    pred: str = "pred",
) -> pl.DataFrame:
    """Error by market-price tier.

    The tail matters more than the average here. A model that is accurate on
    early-round players and wild on late-round ones is exactly backwards for
    sleeper detection, where the whole point lives in the cheap tiers.
    """
    ranked = df.filter(pl.col(value_col).is_not_null()).with_columns(
        ((pl.col(value_col).rank("ordinal") - 1) * n_tiers // pl.len())
        .alias("_tier")
    )
    out = segment_errors(ranked, "_tier", actual=actual, pred=pred, min_n=10)
    return out.rename({"_tier": f"{value_col}_tier"}) if out.height else out


def worst_misses(
    df: pl.DataFrame,
    n: int = 20,
    actual: str = "actual",
    pred: str = "pred",
    label: str = "player_name",
) -> pl.DataFrame:
    """The individual rows the model got most wrong, both directions.

    Read these by name. Football knowledge is a real diagnostic tool: if the
    misses share an obvious cause (all rookies, all injured, all one team),
    that is a feature you are missing, not irreducible error.
    """
    scored = df.with_columns((pl.col(actual) - pl.col(pred)).alias("_err"))
    cols = [c for c in (label, "season", "position", actual, pred, "_err")
            if c in scored.columns]
    under = scored.sort("_err", descending=True).head(n).select(cols)
    over = scored.sort("_err").head(n).select(cols)
    return pl.concat([
        under.with_columns(pl.lit("under-predicted").alias("direction")),
        over.with_columns(pl.lit("over-predicted").alias("direction")),
    ])


# --------------------------------------------------------------------------
# Quantile models — is the floor actually a floor?
# --------------------------------------------------------------------------

def quantile_calibration(
    df: pl.DataFrame,
    actual: str = "actual",
    quantile_cols: dict[float, str] | None = None,
) -> pl.DataFrame:
    """Do the predicted quantiles contain the right share of outcomes?

    A q20 prediction should sit above the actual result 20% of the time. If
    only 8% of actuals fall below it, the "floor" is not a floor and every
    risk claim built on it is wrong. This is the check that decides whether
    the volatility pillar is trustworthy.
    """
    if quantile_cols is None:
        quantile_cols = {q: f"q{int(q * 100)}" for q in config.QUANTILES}

    rows = []
    for q, col in sorted(quantile_cols.items()):
        if col not in df.columns:
            continue
        below = df.filter(pl.col(actual) <= pl.col(col)).height
        empirical = below / df.height if df.height else 0.0
        rows.append({
            "quantile": q,
            "expected_coverage": q,
            "empirical_coverage": round(empirical, 4),
            "error": round(empirical - q, 4),
            "verdict": "ok" if abs(empirical - q) < 0.05 else "MISCALIBRATED",
        })
    return pl.DataFrame(rows)


def discrimination(
    preds: pl.DataFrame,
    actual_spread: str = "weekly_sd",
    lo: str = "q20",
    hi: str = "q80",
    by: str = "position",
    n_buckets: int = 5,
) -> dict:
    """Does the risk model identify WHICH players are volatile?

    Calibration and discrimination are different properties and calibration
    is the weaker one. A model that assigns every player the same spread can
    be perfectly calibrated -- 20% of weeks really do fall below q20 -- while
    being useless for picking, because it never says anyone is riskier than
    anyone else.

    This asks the sharper question: rank players by predicted spread, then
    check whether the actual spread rises across those buckets. Flat buckets
    mean no discrimination regardless of how good the calibration looks.

    Correlation is computed within position, since a TE and a QB differ in
    scale for reasons that have nothing to do with volatility.
    """
    d = preds.with_columns((pl.col(hi) - pl.col(lo)).alias("_pred_spread"))
    d = d.filter(
        pl.col("_pred_spread").is_not_null() & pl.col(actual_spread).is_not_null()
    )
    if d.height < 50:
        return {"error": "not enough rows"}

    overall_r = d.select(pl.corr("_pred_spread", actual_spread)).item()

    within = {}
    if by in d.columns:
        for (pos,), grp in d.group_by([by]):
            if grp.height >= 30:
                r = grp.select(pl.corr("_pred_spread", actual_spread)).item()
                within[pos] = round(r, 4) if r is not None else None

    buckets = (
        d.with_columns(
            ((pl.col("_pred_spread").rank("ordinal") - 1)
             * n_buckets // pl.len()).alias("_b")
        )
        .group_by("_b")
        .agg([
            pl.len().alias("n"),
            pl.col("_pred_spread").mean().round(2).alias("mean_predicted_spread"),
            pl.col(actual_spread).mean().round(2).alias("mean_actual_spread"),
        ])
        .sort("_b")
    )

    lo_b = float(buckets["mean_actual_spread"][0])
    hi_b = float(buckets["mean_actual_spread"][-1])

    return {
        "n": d.height,
        "spread_corr_overall": round(overall_r, 4) if overall_r else None,
        "spread_corr_within_position": within,
        "buckets": buckets,
        "lowest_bucket_actual": round(lo_b, 2),
        "highest_bucket_actual": round(hi_b, 2),
        "separation": round(hi_b - lo_b, 2),
        "reading": (
            "corr near 0 or flat buckets = no discrimination; the model is "
            "calibrated but cannot tell you who is boom-or-bust"
        ),
    }


def interval_width(
    df: pl.DataFrame, lo: str = "q20", hi: str = "q80", by: str = "position"
) -> pl.DataFrame:
    """Predicted floor-to-ceiling spread by group — the boom/bust readout."""
    return (
        df.with_columns((pl.col(hi) - pl.col(lo)).alias("width"))
        .group_by(by)
        .agg([
            pl.len().alias("n"),
            pl.col("width").mean().round(2).alias("mean_width"),
            pl.col("width").median().round(2).alias("median_width"),
        ])
        .sort("mean_width", descending=True)
    )


# --------------------------------------------------------------------------
# Stability across folds
# --------------------------------------------------------------------------

def fold_stability(fold_results: dict[int, dict]) -> pl.DataFrame:
    """Per-fold metrics plus spread.

    A config that averages well but swings hard fold to fold is fragile, not
    good. Compare configs on the spread as well as the mean.
    """
    rows = [{"validation_season": season, **metrics}
            for season, metrics in sorted(fold_results.items())]
    df = pl.DataFrame(rows)
    if "mae" in df.columns and df.height > 1:
        maes = df["mae"].to_numpy()
        print(f"  mean MAE {maes.mean():.3f}  sd {maes.std():.3f}  "
              f"range {maes.min():.3f}-{maes.max():.3f}")
        if config.COVID_SEASON in df["validation_season"].to_list():
            print(f"  note: {config.COVID_SEASON} is the COVID fold — expect an outlier")
    return df


# --------------------------------------------------------------------------
# Does it find value the market missed?
# --------------------------------------------------------------------------

def rank_head_to_head(
    df: pl.DataFrame,
    model_rank: str = "model_rank",
    market_rank: str = "market_rank",
    actual_rank: str = "actual_finish",
) -> dict:
    """Whose ranking lands closer to the real finish — model or market?

    This is the decisive test, and it is stricter than `value_gap_hit_rate`
    below. That one asks "did players the model liked finish better than the
    market said?", which mostly measures how often the *market* was wrong and
    can read well above 50% even when the model adds nothing. This asks the
    direct question instead: put both rankings next to the truth and see which
    is nearer.

    Ranks arrive from `rank()` as UInt32 and subtracting two of them
    underflows to 2**32, so cast before differencing.
    """
    d = df.with_columns([
        pl.col(model_rank).cast(pl.Int32),
        pl.col(market_rank).cast(pl.Int32),
        pl.col(actual_rank).cast(pl.Int32),
    ])
    model_err = float((d[model_rank] - d[actual_rank]).abs().mean())
    market_err = float((d[market_rank] - d[actual_rank]).abs().mean())
    beat = float(
        d.with_columns(
            ((pl.col(model_rank) - pl.col(actual_rank)).abs()
             < (pl.col(market_rank) - pl.col(actual_rank)).abs()).alias("_w")
        )["_w"].mean()
    )
    return {
        "n": d.height,
        "model_mean_rank_error": round(model_err, 2),
        "market_mean_rank_error": round(market_err, 2),
        "closer": "model" if model_err < market_err else "market",
        "pct_players_model_closer": round(100 * beat, 1),
        "model_rank_corr": round(
            d.select(pl.corr(model_rank, actual_rank)).item(), 4),
        "market_rank_corr": round(
            d.select(pl.corr(market_rank, actual_rank)).item(), 4),
        "reading": "50% and equal errors means no edge over the market",
    }


def value_gap_hit_rate(
    df: pl.DataFrame,
    gap_col: str = "value_gap",
    actual_rank: str = "actual_finish",
    market_rank: str = "ecr",
    threshold: int = 12,
) -> dict:
    """The question the whole project exists to answer.

    When the model disagreed with the market, who was right? A hit rate near
    50% means the model adds nothing over ADP no matter how good its MAE is —
    it would be beating the market on points while being useless for picks.
    """
    scoped = df.filter(
        pl.col(gap_col).is_not_null()
        & pl.col(actual_rank).is_not_null()
        & pl.col(market_rank).is_not_null()
    )
    if not scoped.height:
        return {"error": "no rows with both model and market values"}

    calls = scoped.filter(pl.col(gap_col).abs() >= threshold)
    if not calls.height:
        return {"error": f"no calls with |gap| >= {threshold}"}

    # Model said cheap (positive gap) and player finished better than market
    # rank => model won. Ranks are lower-is-better.
    won = calls.filter(
        ((pl.col(gap_col) > 0) & (pl.col(actual_rank) < pl.col(market_rank)))
        | ((pl.col(gap_col) < 0) & (pl.col(actual_rank) > pl.col(market_rank)))
    ).height

    return {
        "calls_made": calls.height,
        "model_correct": won,
        "hit_rate": round(100 * won / calls.height, 1),
        "threshold": threshold,
        "reading": "50% = no edge over ADP",
    }


# --------------------------------------------------------------------------
# Feature attribution
# --------------------------------------------------------------------------

def feature_importance(model, feature_names: list[str], top: int = 25) -> pl.DataFrame:
    """Gain-based importance. Fast, but only says *how much*, not *which way*."""
    booster = model.get_booster() if hasattr(model, "get_booster") else model
    scores = booster.get_score(importance_type="gain")
    rows = [{"feature": f, "gain": round(scores.get(f, 0.0), 2)} for f in feature_names]
    return pl.DataFrame(rows).sort("gain", descending=True).head(top)


def shap_summary(model, X, feature_names: list[str], top: int = 25) -> pl.DataFrame:
    """Mean |SHAP| plus signed direction.

    Use this to sanity-check *why* a feature helps. A feature that improves
    MAE for an incoherent reason is a feature that will stop helping — check
    that the direction makes football sense before keeping it.
    """
    try:
        # Optional on purpose -- SHAP pulls a large tree of its own and is only
        # ever needed when auditing why a feature helps, never at draft time.
        # The type-ignore is what stops editors underlining it as missing.
        import shap  # type: ignore[import-not-found]
    except ImportError:
        raise ImportError("pip install 'fantasyedge[viz]' for SHAP")

    values = shap.TreeExplainer(model).shap_values(X)
    return (
        pl.DataFrame({
            "feature": feature_names,
            "mean_abs_shap": np.abs(values).mean(axis=0).round(4),
            "mean_shap": values.mean(axis=0).round(4),
        })
        .sort("mean_abs_shap", descending=True)
        .head(top)
    )


# --------------------------------------------------------------------------
# Console report
# --------------------------------------------------------------------------

def print_report(
    df: pl.DataFrame,
    fold_results: dict[int, dict] | None = None,
    model=None,
    feature_names: list[str] | None = None,
) -> None:
    """Everything above, in reading order, to stdout."""
    line = "=" * 68

    print(f"\n{line}\nOVERALL\n{line}")
    for k, v in overall(df).items():
        print(f"  {k:<14} {v}")

    if fold_results:
        print(f"\n{line}\nFOLD STABILITY\n{line}")
        print(fold_stability(fold_results))

    for col in ("position", "season"):
        if col in df.columns:
            print(f"\n{line}\nERROR BY {col.upper()}\n{line}")
            print(segment_errors(df, col))

    if "ecr" in df.columns:
        print(f"\n{line}\nERROR BY MARKET TIER  (tier 0 = most expensive)\n{line}")
        print(tier_errors(df))

    if any(f"q{int(q * 100)}" in df.columns for q in config.QUANTILES):
        print(f"\n{line}\nQUANTILE CALIBRATION\n{line}")
        print(quantile_calibration(df))

    if model is not None and feature_names:
        print(f"\n{line}\nFEATURE IMPORTANCE (gain)\n{line}")
        print(feature_importance(model, feature_names))

    print(f"\n{line}\nWORST MISSES — read these by name\n{line}")
    print(worst_misses(df, n=10))
