"""Season outcome distribution — the bridge between rate and availability.

THE PROBLEM THIS SOLVES

Two models exist and neither answers the question on its own:

    per-game distribution   q20/q50/q80 from the weekly risk model
    availability            expected games played

Multiplying two point estimates collapses both into a single number and
throws away the thing that actually distinguishes players:

    Hampton   15.08 ppg over  9 games  =  135.7
    Jeanty    14.42 ppg over 17 games  =  245.1

Hampton was the better back per game. Jeanty won the season by 109 points.
A single projected total answers neither "who is better" nor "what could
this season look like" -- it silently averages them.

WHAT THIS DOES

Simulates the season many times, drawing games played and per-game scoring
independently each run, and reports the resulting distribution. That yields a
season floor and ceiling that fold in BOTH sources of risk, which is the
number a draft decision actually needs.

    season p20   what a bad-but-not-catastrophic outcome looks like
    season p50   the median season
    season p80   the upside case
    bust_risk    probability of finishing below replacement level

WHY SIMULATE RATHER THAN COMPUTE

The sum of a random number of random variables has no clean closed form once
the per-game distribution is skewed, which fantasy scoring badly is. Sampling
is simpler to reason about and easy to check against reality.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasyedge import config

N_SIMS = 10_000
MAX_GAMES = 17

# Games played is roughly beta-shaped and heavily left-skewed -- most players
# miss a little, a few miss almost everything. This dispersion is fit from
# historical residuals rather than assumed.
GAMES_DISPERSION = 3.6

# Week-to-week spread when the curve does not carry a measured one, as a
# fraction of the player's own rate. Fantasy weekly scoring is roughly this
# noisy at every level, because touchdowns arrive in lumps.
DEFAULT_WEEKLY_CV = 0.65


def _per_game_sampler(
    q20: float, q50: float, q80: float, rng: np.random.Generator, n: int
) -> np.ndarray:
    """Draw weekly scores from a player's own predicted distribution.

    Interpolates through the three predicted quantiles and extends the tails
    linearly. Deliberately keeps the asymmetry: fantasy scoring is
    right-skewed because touchdowns arrive in lumps, and forcing a symmetric
    distribution would understate ceilings and overstate floors.
    """
    u = rng.random(n)
    lo_w = (q50 - q20) / 0.30   # slope below the median
    hi_w = (q80 - q50) / 0.30   # slope above

    out = np.where(
        u < 0.20,
        q20 - (0.20 - u) * lo_w * 2.0,          # lower tail
        np.where(
            u < 0.50,
            q20 + (u - 0.20) / 0.30 * (q50 - q20),
            np.where(
                u < 0.80,
                q50 + (u - 0.50) / 0.30 * (q80 - q50),
                q80 + (u - 0.80) * hi_w * 2.0,   # upper tail
            ),
        ),
    )
    return np.clip(out, 0.0, None)


def simulate_player(
    q20: float,
    q50: float,
    q80: float,
    expected_games: float,
    weekly_sd: float | None = None,
    n_sims: int = N_SIMS,
    seed: int = config.RANDOM_SEED,
) -> dict:
    """Season distribution for one player.

    TWO VARIANCES, NOT ONE

    This used to draw all seventeen weeks straight from q20/q50/q80. Those
    quantiles are a CROSS-SECTIONAL spread -- how season-long scoring rates
    vary across the players who share a rank -- and treating them as
    week-to-week noise averages them away: seventeen independent draws from a
    wide distribution sum to something very close to seventeen times its mean.
    The season band came out far too tight, and leave-one-season-out
    calibration showed it, with only 20.7% of real seasons landing inside a
    band meant to hold 60%.

    A season has two sources of uncertainty and they compound differently:

        which player he turns out to be   drawn ONCE, from the curve.
                                          Scales with games, so it dominates.
        which week you are watching       drawn every week, around that rate.
                                          Averages out across a season.

    So the rate is sampled once per simulated season and the weeks vary around
    it. That is the difference between "how good is he" and "how good was he
    on Sunday", and only the first one decides a draft.
    """
    rng = np.random.default_rng(seed)

    # Games: beta around the expectation, scaled to 0..17.
    mean_frac = np.clip(expected_games / MAX_GAMES, 0.02, 0.98)
    a = mean_frac * GAMES_DISPERSION
    b = (1 - mean_frac) * GAMES_DISPERSION
    games = np.rint(rng.beta(a, b, n_sims) * MAX_GAMES).astype(int)

    # Who he turns out to be: one rate per simulated season.
    rate = _per_game_sampler(q20, q50, q80, rng, n_sims)

    # Which week you are watching: noise around that rate. Falls back to a
    # share of the rate itself when no within-player sd is supplied.
    sd = weekly_sd if weekly_sd and weekly_sd > 0 else max(q50, 1.0) * DEFAULT_WEEKLY_CV
    weekly = np.clip(
        rng.normal(rate[:, None], sd, size=(n_sims, MAX_GAMES)), 0.0, None)

    mask = np.arange(MAX_GAMES)[None, :] < games[:, None]
    totals = (weekly * mask).sum(axis=1)

    return {
        "season_p10": float(np.quantile(totals, 0.10)),
        "season_p20": float(np.quantile(totals, 0.20)),
        "season_p50": float(np.quantile(totals, 0.50)),
        "season_p80": float(np.quantile(totals, 0.80)),
        "season_p90": float(np.quantile(totals, 0.90)),
        "season_mean": float(totals.mean()),
        "season_sd": float(totals.std()),
        "expected_games": float(games.mean()),
    }


def simulate_frame(
    df: pl.DataFrame,
    q20: str = "q20",
    q50: str = "q50",
    q80: str = "q80",
    games: str = "expected_games",
    weekly_sd: str = "weekly_sd",
    replacement: dict[str, float] | None = None,
    n_sims: int = N_SIMS,
) -> pl.DataFrame:
    """Season distributions for a whole board.

    `replacement` maps position to the points of the last startable player,
    which turns the simulation into a bust probability: how often does this
    player fail to clear the bar you could have had for free?
    """
    rows = []
    for i, r in enumerate(df.iter_rows(named=True)):
        if any(r.get(c) is None for c in (q20, q50, q80, games)):
            continue
        sim = simulate_player(
            r[q20], r[q50], r[q80], r[games], r.get(weekly_sd),
            n_sims=n_sims, seed=config.RANDOM_SEED + i,
        )
        sim["player_id"] = r.get("player_id")
        rows.append(sim)

    if not rows:
        return pl.DataFrame()

    out = pl.DataFrame(rows).join(
        df.select([c for c in ("player_id", "player_name", "position")
                   if c in df.columns]),
        on="player_id", how="left",
    )

    if replacement:
        expr = pl.lit(None, dtype=pl.Float64)
        for pos, pts in replacement.items():
            expr = pl.when(pl.col("position") == pos).then(pl.lit(pts)).otherwise(expr)
        out = out.with_columns(expr.alias("_repl")).with_columns([
            (pl.col("season_p50") - pl.col("_repl")).alias("median_vor"),
            (pl.col("season_p20") - pl.col("_repl")).alias("floor_vor"),
            (pl.col("season_p80") - pl.col("_repl")).alias("ceiling_vor"),
        ]).drop("_repl")

    return out.with_columns(
        # Width of the plausible season, the single number that separates a
        # safe anchor from a swing pick.
        (pl.col("season_p80") - pl.col("season_p20")).alias("season_range")
    )


def compare(players: list[dict]) -> pl.DataFrame:
    """Side-by-side season distributions. `players` need name/q20/q50/q80/games."""
    rows = []
    for i, p in enumerate(players):
        sim = simulate_player(p["q20"], p["q50"], p["q80"], p["games"],
                              seed=config.RANDOM_SEED + i)
        sim["player"] = p["name"]
        rows.append(sim)
    return pl.DataFrame(rows).select(
        ["player", "season_p20", "season_p50", "season_p80", "season_mean",
         "season_sd", "expected_games"]
    )
