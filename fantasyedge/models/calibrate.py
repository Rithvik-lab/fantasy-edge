"""Quantile calibration — is the predicted floor actually a floor?

THE TEST THAT WAS WRONG

The first version compared predicted weekly quantiles against `weekly_q50`,
a player's median week. Comparing a floor prediction to a central value forces
the answer: of course almost nothing falls below q20 and almost everything
falls below q80. It measured nothing.

The correct test needs actual *weekly outcomes*. A predicted q20 claims "one
week in five will land below this line". So take every real week that player
played and count how often it did.

THE FIX

Quantile regression trained on season-summary labels regresses toward the
mean, which compresses the spread and makes intervals too narrow. Conformal
recalibration corrects this without retraining: measure the offset needed on
held-out data, then shift the predicted quantile by it.

The offset is fit on one slice of validation and measured on another, so the
coverage number is not the number the offset was tuned to hit.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasyedge import config
from fantasyedge.data import pull

TOLERANCE = 0.03  # coverage within +/-3 points of nominal is acceptable


def weekly_outcomes(seasons: list[int]) -> pl.DataFrame:
    """Every regular-season weekly fantasy score, one row per player-week."""
    return (
        pull.load("player_stats")
        .filter(
            pl.col("season").is_in(seasons)
            & (pl.col("season_type") == "REG")
            & pl.col("position").is_in(list(config.MODELED_POSITIONS))
            & pl.col("player_id").is_not_null()
        )
        .select([
            "player_id", "season", "week",
            pl.col("fantasy_points_ppr").alias("week_points"),
        ])
    )


def coverage(
    preds: pl.DataFrame,
    quantile_cols: dict[float, str],
    seasons: list[int] | None = None,
) -> pl.DataFrame:
    """Empirical coverage of each predicted quantile against real weeks.

    `preds` is one row per player-season carrying the predicted quantiles.
    """
    if seasons is None:
        seasons = sorted(preds["season"].unique().to_list())

    weeks = weekly_outcomes(seasons)
    joined = weeks.join(preds, on=["player_id", "season"], how="inner")

    rows = []
    for q, col in sorted(quantile_cols.items()):
        if col not in joined.columns:
            continue
        below = joined.filter(pl.col("week_points") <= pl.col(col)).height
        emp = below / joined.height if joined.height else 0.0
        rows.append({
            "quantile": q,
            "expected": q,
            "empirical": round(emp, 4),
            "error": round(emp - q, 4),
            "player_weeks": joined.height,
            "verdict": "ok" if abs(emp - q) <= TOLERANCE else "MISCALIBRATED",
        })
    return pl.DataFrame(rows)


def fit_offsets(
    preds: pl.DataFrame,
    quantile_cols: dict[float, str],
) -> dict[str, float]:
    """Additive shift per quantile so empirical coverage matches nominal.

    Conformal in spirit: the offset is the gap between the predicted line and
    the true empirical quantile of the residuals, so applying it moves
    coverage onto target by construction.
    """
    seasons = sorted(preds["season"].unique().to_list())
    weeks = weekly_outcomes(seasons)
    joined = weeks.join(preds, on=["player_id", "season"], how="inner")

    offsets: dict[str, float] = {}
    for q, col in sorted(quantile_cols.items()):
        if col not in joined.columns:
            continue
        resid = (joined["week_points"] - joined[col]).to_numpy()
        # Shift so that exactly q of residuals fall at or below zero.
        offsets[col] = float(np.quantile(resid, q))
    return offsets


def apply_offsets(
    preds: pl.DataFrame, offsets: dict[str, float]
) -> pl.DataFrame:
    """Shift predicted quantiles by the fitted offsets."""
    return preds.with_columns([
        (pl.col(col) + off).alias(col) for col, off in offsets.items()
        if col in preds.columns
    ])


def fit_scale_shift(
    preds: pl.DataFrame,
    quantile_cols: dict[float, str],
    center_col: str = "q50",
    grid: tuple[float, ...] = tuple(np.arange(0.50, 1.51, 0.02)),
) -> dict[str, tuple[float, float]]:
    """Fit (scale, shift) per quantile, rescaling the spread about the median.

    A pure additive shift slides the whole line but cannot narrow an interval
    that is simply too wide. Rescaling the distance from the median can:

        adjusted = median + scale * (raw - median) + shift

    Scale is chosen by grid search for coverage closest to nominal, then the
    residual gap is closed with a shift.
    """
    seasons = sorted(preds["season"].unique().to_list())
    weeks = weekly_outcomes(seasons)
    joined = weeks.join(preds, on=["player_id", "season"], how="inner")

    out: dict[str, tuple[float, float]] = {}
    actual = joined["week_points"].to_numpy()
    center = joined[center_col].to_numpy()

    for q, col in sorted(quantile_cols.items()):
        if col not in joined.columns:
            continue
        raw = joined[col].to_numpy()

        best, best_err = 1.0, float("inf")
        for s in grid:
            scaled = center + s * (raw - center)
            err = abs(float((actual <= scaled).mean()) - q)
            if err < best_err:
                best, best_err = float(s), err

        scaled = center + best * (raw - center)
        shift = float(np.quantile(actual - scaled, q))
        out[col] = (best, shift)
    return out


def apply_scale_shift(
    preds: pl.DataFrame,
    params: dict[str, tuple[float, float]],
    center_col: str = "q50",
) -> pl.DataFrame:
    out = preds
    for col, (scale, shift) in params.items():
        if col not in preds.columns:
            continue
        out = out.with_columns(
            (pl.col(center_col) + scale * (pl.col(col) - pl.col(center_col)) + shift)
            .alias(col)
        )
    return out


def fit_offsets_by_position(
    preds: pl.DataFrame,
    quantile_cols: dict[float, str],
    positions: pl.DataFrame,
) -> dict[tuple[str, str], float]:
    """Offsets fit separately per position.

    A single global offset averages over positions whose scoring
    distributions differ substantially -- roughly 16% of player-weeks are
    zero, and that mass is not spread evenly. Fitting per position removes
    the compromise.

    `positions` needs player_id, season, position.
    """
    seasons = sorted(preds["season"].unique().to_list())
    weeks = weekly_outcomes(seasons)
    joined = (
        weeks.join(preds, on=["player_id", "season"], how="inner")
        .join(positions, on=["player_id", "season"], how="left")
        .filter(pl.col("position").is_not_null())
    )

    out: dict[tuple[str, str], float] = {}
    for pos in joined["position"].unique().to_list():
        sub = joined.filter(pl.col("position") == pos)
        if sub.height < 100:
            continue
        for q, col in sorted(quantile_cols.items()):
            if col not in sub.columns:
                continue
            resid = (sub["week_points"] - sub[col]).to_numpy()
            out[(pos, col)] = float(np.quantile(resid, q))
    return out


def apply_offsets_by_position(
    preds: pl.DataFrame,
    offsets: dict[tuple[str, str], float],
    positions: pl.DataFrame,
) -> pl.DataFrame:
    """Apply per-position offsets, leaving unseen positions untouched."""
    out = preds.join(positions, on=["player_id", "season"], how="left")
    cols = {c for _, c in offsets}
    for col in cols:
        expr = pl.col(col)
        for (pos, c), off in offsets.items():
            if c != col:
                continue
            expr = pl.when(pl.col("position") == pos).then(
                pl.col(col) + off
            ).otherwise(expr)
        out = out.with_columns(expr.alias(col))
    return out


def enforce_monotonic(
    preds: pl.DataFrame, ordered_cols: list[str]
) -> pl.DataFrame:
    """Keep q20 <= q50 <= q80 after shifting.

    Independently fitted quantile models can cross, which would let a ceiling
    sit below a floor. Cumulative max fixes it cheaply.
    """
    out = preds
    for lo, hi in zip(ordered_cols, ordered_cols[1:]):
        out = out.with_columns(
            pl.max_horizontal([pl.col(lo), pl.col(hi)]).alias(hi)
        )
    return out


def calibrate(
    preds: pl.DataFrame,
    quantile_cols: dict[float, str],
    fit_seasons: list[int],
    test_seasons: list[int],
) -> tuple[pl.DataFrame, dict[str, float], pl.DataFrame, pl.DataFrame]:
    """Fit offsets on one set of seasons, measure coverage on another.

    Fitting and measuring on the same rows would report the target back at
    you regardless of whether it generalises.
    """
    fit_part = preds.filter(pl.col("season").is_in(fit_seasons))
    test_part = preds.filter(pl.col("season").is_in(test_seasons))

    before = coverage(test_part, quantile_cols, test_seasons)
    offsets = fit_offsets(fit_part, quantile_cols)

    adjusted = apply_offsets(test_part, offsets)
    adjusted = enforce_monotonic(
        adjusted, [quantile_cols[q] for q in sorted(quantile_cols)]
    )
    after = coverage(adjusted, quantile_cols, test_seasons)

    return adjusted, offsets, before, after
