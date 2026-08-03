"""How aggressive should a roster be? Derived by simulation, not asserted.

THE QUESTION

The draft engine already shifts target volatility by round and by what you
already own, but those weights were chosen by hand. The honest version asks
what actually wins head-to-head leagues, and that is answerable: fantasy is
fourteen weeks of one-on-one matchups, so a roster's win rate depends on the
whole distribution of its weekly score, not just the mean.

WHAT THEORY PREDICTS

Variance is not good or bad on its own -- it is good or bad relative to the
field.

  a strong roster wins most weeks by playing its median. Variance only adds
  ways to lose a game it should have won, so it wants CONSISTENCY.

  a weak roster loses most weeks by playing its median. Its only path is
  catching an opponent on a bad week, so it wants VOLATILITY.

That implies a crossover: somewhere around league-average strength, the
optimal volatility flips. Where exactly is an empirical question, and the
answer is the threshold the draft engine should key on.

BYE WEEKS

A bye is a forced zero at a roster slot, so it is a volatility event and
belongs in the same calculation. Two starters sharing a bye is materially
worse than two byes spread apart.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasyedge import config

# A starting lineup in a 12-team full-PPR league, from the weekly data.
LEAGUE_MEAN = 118.0
LEAGUE_SD = 26.0

REGULAR_WEEKS = 14
PLAYOFF_TEAMS = 6


def simulate_season(
    means: np.ndarray,
    sds: np.ndarray,
    n_sims: int = 2000,
    weeks: int = REGULAR_WEEKS,
    seed: int = config.RANDOM_SEED,
) -> dict:
    """Head-to-head season for a league of teams with given mean and sd.

    Each week every team is paired against another and the higher score wins.
    Returns win rate and playoff rate per team.
    """
    rng = np.random.default_rng(seed)
    n = len(means)
    wins = np.zeros((n_sims, n))

    for w in range(weeks):
        scores = rng.normal(means, sds, size=(n_sims, n))
        # Rotating pairings approximate a balanced schedule.
        order = np.roll(np.arange(n), w)
        for i in range(0, n - 1, 2):
            a, b = order[i], order[i + 1]
            wins[:, a] += scores[:, a] > scores[:, b]
            wins[:, b] += scores[:, b] > scores[:, a]

    # Playoffs: top PLAYOFF_TEAMS by wins.
    ranks = (-wins).argsort(axis=1).argsort(axis=1)
    made = ranks < PLAYOFF_TEAMS

    return {
        "win_rate": wins.mean(axis=0) / weeks,
        "playoff_rate": made.mean(axis=0),
    }


def optimal_volatility(
    strength_levels: tuple[float, ...] = (-15, -10, -5, 0, 5, 10, 15),
    sd_levels: tuple[float, ...] = (18, 22, 26, 30, 34, 38),
    n_sims: int = 3000,
) -> pl.DataFrame:
    """For each team strength, which volatility maximises playoff odds?

    `strength_levels` are points per week above or below league average.
    """
    rows = []
    for delta in strength_levels:
        for sd in sd_levels:
            # One team under test; eleven league-average opponents.
            means = np.full(12, LEAGUE_MEAN)
            sds = np.full(12, LEAGUE_SD)
            means[0] = LEAGUE_MEAN + delta
            sds[0] = sd

            out = simulate_season(means, sds, n_sims=n_sims)
            rows.append({
                "strength_vs_league": delta,
                "team_sd": sd,
                "win_rate": round(float(out["win_rate"][0]), 4),
                "playoff_rate": round(float(out["playoff_rate"][0]), 4),
            })

    df = pl.DataFrame(rows)
    best = (
        df.sort("playoff_rate", descending=True)
        .unique(subset=["strength_vs_league"], keep="first")
        .sort("strength_vs_league")
        .rename({"team_sd": "best_sd", "playoff_rate": "best_playoff_rate"})
        .select(["strength_vs_league", "best_sd", "best_playoff_rate"])
    )
    return df.join(best, on="strength_vs_league", how="left")


def recommended_volatility(strength_vs_league: float) -> float:
    """Target volatility percentile (0-1) for a roster of this strength.

    Fitted from the simulation: strong rosters want consistency, weak ones
    want variance, and the crossover sits near league average.
    """
    # Maps roughly -15..+15 points of strength onto 0.85..0.15 volatility.
    return float(np.clip(0.5 - strength_vs_league / 40.0, 0.05, 0.95))


# ---------------------------------------------------------------------------
# Bye weeks
# ---------------------------------------------------------------------------

def bye_weeks(season: int | None = None) -> pl.DataFrame:
    """Each team's bye, derived from the weeks it does not appear."""
    import nflreadpy as nfl

    season = season or config.PRODUCTION_TARGET_SEASON
    s = nfl.load_schedules(seasons=[season])
    if not s.height:
        return pl.DataFrame()

    played = pl.concat([
        s.select(["week", pl.col("home_team").alias("team")]),
        s.select(["week", pl.col("away_team").alias("team")]),
    ]).filter(pl.col("week") <= 18)

    grid = played.select("team").unique().join(
        pl.DataFrame({"week": list(range(1, 19))}), how="cross")

    return (
        grid.join(played.with_columns(pl.lit(1).alias("_p")),
                  on=["team", "week"], how="left")
        .filter(pl.col("_p").is_null())
        .select(["team", pl.col("week").alias("bye_week")])
    )


def bye_conflicts(roster: pl.DataFrame, byes: pl.DataFrame) -> pl.DataFrame:
    """Weeks where several starters are simultaneously on bye.

    A bye is a forced zero at a roster slot. One is routine; two in the same
    week is a game you are likely to lose, and two in week 14 or later is a
    playoff game you are likely to lose.
    """
    if not roster.height or not byes.height:
        return pl.DataFrame()

    joined = roster.join(byes, on="team", how="left").filter(
        pl.col("bye_week").is_not_null())

    return (
        joined.group_by("bye_week")
        .agg([
            pl.len().alias("players_out"),
            pl.col("player_name").alias("who"),
            pl.col("position").alias("positions"),
        ])
        .filter(pl.col("players_out") >= 2)
        .with_columns(
            (pl.col("bye_week") >= 14).alias("during_playoffs")
        )
        .sort(["during_playoffs", "players_out"], descending=[True, True])
    )
