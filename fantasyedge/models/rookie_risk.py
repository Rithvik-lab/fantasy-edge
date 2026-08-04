"""Rookies are not more volatile. They are more likely to return nothing.

THE BUG THIS FIXES

The draft board slots every player by projected positional rank and then reads
the same per-game curve to get a season distribution. That curve is built from
players who *finished* at each rank, so it describes an established producer.
A rookie slotted at RB8 inherited RB8's historical spread -- as if two hundred
NFL carries were already on his record.

The effect was not merely neutral, it was backwards. Because that curve is
tight, the 2026 rookies came out among the SAFEST players at their position:

    Jeremiyah Love   RB   volatility percentile 0.15
    Carnell Tate     WR   volatility percentile 0.10
    Kenyon Sadiq     TE   volatility percentile 0.10

Early rounds target low volatility, so the engine was handing unproven rookies
a *bonus* for having no track record.

WHAT THE DATA ACTUALLY SAYS

Comparing rookies against veterans at matched projections (rookie projection
from draft capital, veteran projection from prior-season positional rank, both
leave-one-season-out), total spread is the same -- weighted sd ratio 0.98.
"Rookies are volatile" is not the finding, and had we shipped a blanket
variance inflation it would have been wrong.

The difference is entirely in the LEFT TAIL, and only at the top of the board.
Outcome as a multiple of projection, by projection tier:

    tier            n     mean    p20     p90    P(< 0.5x)
    late    vet   3089    0.99   0.143   2.35     41.1%
            rook   411    0.93   0.053   2.56     47.2%
    mid     vet   2053    0.97   0.382   1.85     26.7%
            rook   186    1.04   0.445   1.88     23.7%
    early   vet   1768    0.99   0.621   1.53     13.7%
            rook    85    0.88   0.333   1.49     24.7%

At the top of the board a rookie busts at 1.8x the veteran rate -- bootstrap
gap +11.0pp, 95% CI [+1.8pp, +20.8pp], P(gap > 0) = 99.0% over 5,000 resamples
-- while his 90th percentile is 1.49x projection against the veteran's 1.53x.
Fatter downside with no extra upside to pay for it. That is a strictly worse
bet, and it is worst exactly where it costs the most.

Note the MID tier shows no harm at all, so the adjustment is interpolated
between measured tier midpoints rather than ramped monotonically. Applying a
single ramp would have penalised a group the data says is fine.

The shipped rookie model shows the same thing on its own walk-forward
predictions: among rookies it projected at 120+ points, mean prediction 163
against mean outcome 148, and 26.4% returned less than half. Travis Hunter in
2025 is the archetype -- projected 240, scored 0.

WHY THIS IS ROUND-DEPENDENT FOR FREE

Widening only the downside raises a rookie's coefficient of variation, which
raises his volatility percentile, which the existing `risk_fit` machinery
already penalises in early rounds (target volatility ~0.3) and stops
penalising in late ones (target ~0.8). The round dependence falls out of
the risk model already in place; nothing here special-cases a round number.
"""

from __future__ import annotations

import numpy as np
import polars as pl

# Midpoint of each measured projection tier. Adjustments are interpolated
# between these, so every number below is traceable to a row of the table.
TIER_MIDPOINTS = (65.0, 115.0, 200.0)

# Mean outcome relative to the veteran at the same projection:
#   late  0.932 / 0.994 = 0.94
#   mid   1.040 / 0.973 = 1.07  -> clipped to 1.00
#   early 0.876 / 0.987 = 0.89
# The mid-tier value is clipped because an n=186 cell is not enough evidence
# to make the engine actively PREFER a rookie. The asymmetry is deliberate:
# this module exists to stop rookies being flattered, not to flatter them.
MEAN_DISCOUNTS = (0.94, 1.00, 0.89)

# 20th-percentile season relative to the veteran at the same projection:
#   late  0.053 / 0.143 = 0.37  -> damped to 0.75
#   mid   0.445 / 0.382 = 1.16  -> clipped to 1.00
#   early 0.333 / 0.621 = 0.54
# The late-tier ratio is damped rather than taken literally: both groups' p20
# is within a rounding error of zero there, so the ratio is unstable and the
# honest reading is "somewhat worse", not "three times worse".
FLOOR_DISCOUNTS = (0.75, 1.00, 0.54)

# Draft-day position. The board previously gave every rookie a flat sd of 4.5,
# tighter than the veterans around them (6.2 inside pick 60, 14.6 from 60-120)
# -- claiming rookie draft position is MORE predictable than a veteran's.
# Rookie ADP is the least settled on the board, so it should be the widest.
ROOKIE_SD_MULTIPLIER = 1.6
MIN_ROOKIE_SD = 7.0


def projection_discount(projected_points: float | None) -> float:
    """Multiplier on a rookie's projected season points."""
    if projected_points is None:
        return 1.0
    return float(np.interp(projected_points, TIER_MIDPOINTS, MEAN_DISCOUNTS))


def floor_discount(projected_points: float | None) -> float:
    """Multiplier on a rookie's season floor -- the left tail only.

    Applied to the 20th-percentile season while the median and ceiling stay
    put, because that is the shape the data shows: same width overall, worse
    downside. Widening both tails would have overstated rookie upside, which
    the measurement explicitly rules out (p90 ratio 1.49 vs 1.53).
    """
    if projected_points is None:
        return 1.0
    return float(np.interp(projected_points, TIER_MIDPOINTS, FLOOR_DISCOUNTS))


def apply(board: pl.DataFrame, points_col: str = "projected_points") -> pl.DataFrame:
    """Discount rookie projections. Call before the season simulation.

    A no-op on boards without a `rookie` flag, so it is safe to call blind.
    """
    if "rookie" not in board.columns or points_col not in board.columns:
        return board
    disc = pl.col(points_col).map_elements(projection_discount,
                                           return_dtype=pl.Float64)
    return board.with_columns(
        pl.when(pl.col("rookie").fill_null(False))
        .then(pl.col(points_col) * disc)
        .otherwise(pl.col(points_col))
        .alias(points_col)
    )


def apply_floor(board: pl.DataFrame, floor_col: str = "season_p20",
                points_col: str = "projected_points") -> pl.DataFrame:
    """Drop rookie season floors, leaving median and ceiling alone.

    `season_range` is recomputed so the volatility percentile downstream sees
    the widened band.
    """
    need = {"rookie", floor_col, points_col, "season_p80"}
    if not need.issubset(board.columns):
        return board
    disc = pl.col(points_col).map_elements(floor_discount, return_dtype=pl.Float64)
    return board.with_columns(
        pl.when(pl.col("rookie").fill_null(False))
        .then(pl.col(floor_col) * disc)
        .otherwise(pl.col(floor_col))
        .alias(floor_col)
    ).with_columns(
        (pl.col("season_p80") - pl.col(floor_col)).alias("season_range")
    )


def draft_sigma(ecr: float | None, base_sd: float | None) -> float:
    """Draft-day sigma for a rookie: wider than the veteran beside him."""
    vet_like = base_sd if base_sd else (2.0 + (ecr or 60.0) * 0.12)
    return max(MIN_ROOKIE_SD, vet_like * ROOKIE_SD_MULTIPLIER)


# ---------------------------------------------------------------------------
# The measurement behind the constants above
# ---------------------------------------------------------------------------

def measure(bootstrap: int = 5000, seed: int = 0) -> dict:
    """Re-derive the rookie/veteran gap from the data. Prints and returns it.

    Kept in the module so the constants can be checked rather than trusted --
    a future season's data may move them.
    """
    pos = ["QB", "RB", "WR", "TE"]

    vet = (
        pl.read_parquet("data/processed/features.parquet")
        .filter(pl.col("position").is_in(pos)
                & pl.col("prior_total_points").is_not_null())
        .with_columns(pl.col("prior_total_points").rank("ordinal", descending=True)
                      .over(["season", "position"]).cast(pl.Int32).alias("anchor"))
        .select(["season", "position", "player_name", "total_points", "anchor"])
    )
    rk = (
        pl.read_parquet("data/processed/rookies.parquet")
        .filter(pl.col("position").is_in(pos) & (pl.col("season") <= 2025))
        .with_columns((pl.col("round").cast(pl.Int32) * 10
                       + pl.col("pos_draft_rank").cast(pl.Int32).clip(1, 9))
                      .alias("anchor"))
        .select(["season", "position", "player_name",
                 pl.col("rookie_points").alias("total_points"), "anchor"])
    )

    def loso(df, min_cell=6):
        out = []
        for s in df["season"].unique().sort():
            tr, te = df.filter(pl.col("season") != s), df.filter(pl.col("season") == s)
            tbl = (tr.group_by(["position", "anchor"])
                   .agg(pl.col("total_points").mean().alias("proj"), pl.len().alias("_n"))
                   .filter(pl.col("_n") >= min_cell).drop("_n"))
            out.append(te.join(tbl, on=["position", "anchor"], how="inner"))
        return pl.concat(out)

    both = pl.concat([
        loso(vet).with_columns(pl.lit("vet").alias("grp")),
        loso(rk).with_columns(pl.lit("rookie").alias("grp")),
    ]).with_columns((pl.col("total_points") / pl.col("proj")).alias("ratio"))

    tiers = ((40, 90, "late"), (90, 150, "mid"), (150, 400, "early"))
    rows = []
    for lo, hi, lbl in tiers:
        for g in ("vet", "rookie"):
            d = both.filter((pl.col("grp") == g) & pl.col("proj").is_between(lo, hi))
            if d.height < 25:
                continue
            v = d["ratio"].to_numpy()
            rows.append({
                "tier": lbl, "grp": g, "n": d.height,
                "mean_ratio": round(float(v.mean()), 3),
                "p10_ratio": round(float(np.quantile(v, .10)), 3),
                "p20_ratio": round(float(np.quantile(v, .20)), 3),
                "p90_ratio": round(float(np.quantile(v, .90)), 3),
                "bust_rate": round(float((v < 0.5).mean()), 3),
            })
    table = pl.DataFrame(rows)
    print(table)

    sub = both.filter(pl.col("proj") >= 150)
    vv = sub.filter(pl.col("grp") == "vet")["ratio"].to_numpy()
    rv = sub.filter(pl.col("grp") == "rookie")["ratio"].to_numpy()
    rng = np.random.default_rng(seed)
    gaps = np.array([
        np.mean(rng.choice(rv, len(rv)) < 0.5) - np.mean(rng.choice(vv, len(vv)) < 0.5)
        for _ in range(bootstrap)
    ])
    out = {
        "table": table,
        "bust_gap": round(float(gaps.mean()), 4),
        "bust_gap_ci": (round(float(np.quantile(gaps, .025)), 4),
                        round(float(np.quantile(gaps, .975)), 4)),
        "p_gap_positive": round(float((gaps > 0).mean()), 4),
    }
    print(f"\ntop-of-board bust gap {out['bust_gap']:+.1%} "
          f"95% CI [{out['bust_gap_ci'][0]:+.1%}, {out['bust_gap_ci'][1]:+.1%}]  "
          f"P(>0)={out['p_gap_positive']:.1%}")
    return out


if __name__ == "__main__":
    measure()
