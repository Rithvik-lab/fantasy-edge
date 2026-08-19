"""Injury history features and an honest view of what they can predict.

WHAT THE EVIDENCE SUPPORTS

Predicting *who* gets injured is close to hopeless — most NFL injuries are
acute contact trauma, which is close to random. Predicting *expected games
missed* is a different and much more tractable question, and it is the one
fantasy actually needs. You are not asking "will he tear an ACL", you are
asking "how many games do I get from this roster slot".

This is why the NBA intuition transfers badly. NBA injury-proneness is
genuinely more predictable because so much of it is chronic and degenerative
— the same knee, the same back, load-managed across an 82-game season. NFL
injury is dominated by collisions. Different mechanism, different ceiling on
predictability.

Signals that hold up:

  prior games missed        the base rate persists year over year
  same-body-part recurrence the strongest single feature available.
                            soft-tissue injuries recur at high rates.
  age, especially RB age    interacts with cumulative workload
  workload spike            a large jump over a player's own baseline
  position                  RB > WR > TE > QB for games missed
  practice participation    excellent for next-week availability,
                            much weaker for season-long projection

Signals that mostly do not:

  "injury prone" reputation  usually recency and survivorship bias
  turf vs grass              real but small, and contested
  a single prior injury      one bad break says little about next year

HOW TO JUDGE THE RESULT

Always compare against the naive baseline: predicted games missed from
position and age alone. Beating that baseline is the bar. If the model does
not clear it, report that — "I tested whether injury history predicts
availability and it barely does" is a legitimate and more interesting finding
than a model with an unexamined R-squared.

MEASURED, 2011-2025, 8,585 player-seasons
─────────────────────────────────────────

That is what happened. The test above is `persistence_check` and it says:

    year-over-year r on games missed    0.130      (7,877 paired seasons)

against this module's own scale, written before the test was run: "<0.15
mostly noise, use position/age base rates instead". So injury history barely
predicts next-season availability, and nothing here feeds the board. Expected
games comes from the per-game curve, which reads realised availability off
pre-season rank — the position/age base rate this file recommends as the
fallback, arrived at from the other direction.

The soft-tissue recurrence flag is DELETED rather than dormant, and how it
died is worth keeping. It never fired once: `injury_panel` filtered on
`season_type == "REG"`, nflverse only began populating that column in 2025, so
fourteen of fifteen seasons were silently discarded and a flag that needs LAST
season's soft-tissue weeks had no last season. With the filter fixed it fires
269 times and points backwards:

    back-to-back soft tissue     4.67 games missed the next season
    everyone else                6.73

Which is not a discovery about hamstrings. To appear on the injury report in
consecutive seasons you have to be rostered and playing in both, so the flag
selects for durable established starters — the survivorship bias this file
warned about under "signals that mostly do not", found in its own feature.

A feature that has to be repaired before it can be wrong is a feature nobody
was reading. The repair stays; the flag does not.
"""

from __future__ import annotations

import polars as pl

from fantasyedge import config
from fantasyedge.data import pull

# Injuries with high documented recurrence rates. Recurrence is the mechanism
# that makes injury history predictive at all.
SOFT_TISSUE = ("hamstring", "groin", "quad", "calf", "hip flexor", "abdomen")

# Injuries that tend to depress performance after return, not just availability.
STRUCTURAL = ("acl", "achilles", "lisfranc", "patell", "meniscus", "fracture")

DNP = "Did Not Participate In Practice"
LIMITED = "Limited Participation in Practice"


def _norm(col: str) -> pl.Expr:
    return pl.col(col).str.to_lowercase().fill_null("")


def _contains_any(col: str, needles: tuple[str, ...]) -> pl.Expr:
    expr = pl.lit(False)
    for n in needles:
        expr = expr | _norm(col).str.contains(n, literal=True)
    return expr


def injury_panel(seasons: list[int]) -> pl.DataFrame:
    """Per player-season summary of the weekly injury report.

    `injuries` stores season and week as Float, unlike the rest of nflverse.
    Cast before anything else touches it.
    """
    inj = (
        pull.load("injuries")
        .with_columns([
            pl.col("season").cast(pl.Int32, strict=False),
            pl.col("week").cast(pl.Int32, strict=False),
        ])
        .filter(
            pl.col("season").is_in(seasons)
            & pl.col("gsis_id").is_not_null()
            # A NULL SEASON TYPE IS NOT A POST-SEASON GAME. nflverse only
            # started populating this column in 2025, so `== "REG"` was true
            # for one season out of fifteen and quietly discarded the rest --
            # which is why `soft_tissue_recurrence` never once fired: it needs
            # last season's soft-tissue weeks, and there was no last season.
            & (pl.col("season_type") != "POST").fill_null(True)
        )
        .with_columns([
            _contains_any("report_primary_injury", SOFT_TISSUE).alias("_soft"),
            _contains_any("report_primary_injury", STRUCTURAL).alias("_struct"),
            (pl.col("report_status") == "Out").alias("_out"),
            (pl.col("practice_status") == DNP).alias("_dnp"),
            (pl.col("practice_status") == LIMITED).alias("_limited"),
        ])
    )

    return (
        inj.group_by(["gsis_id", "season"])
        .agg([
            pl.len().alias("injury_report_weeks"),
            pl.col("_out").sum().alias("weeks_listed_out"),
            pl.col("_dnp").sum().alias("weeks_dnp"),
            pl.col("_limited").sum().alias("weeks_limited"),
            pl.col("_soft").sum().alias("weeks_soft_tissue"),
            pl.col("_struct").sum().alias("weeks_structural"),
            # Distinct body parts — a proxy for "banged up all over" vs one
            # nagging problem, which behave differently.
            pl.col("report_primary_injury").n_unique().alias("distinct_injuries"),
            pl.col("report_primary_injury").drop_nulls().unique()
              .alias("injury_list"),
        ])
        .rename({"gsis_id": "player_id"})
    )


def availability_panel(seasons: list[int]) -> pl.DataFrame:
    """Games played vs games available — the label for the availability model.

    Team games played comes from the schedule, so a player on a bye-heavy or
    shortened season is not penalised for games that did not exist.
    """
    stats = (
        pull.load("player_stats")
        .filter(
            pl.col("season").is_in(seasons)
            & (pl.col("season_type") == "REG")
            & pl.col("position").is_in(list(config.MODELED_POSITIONS))
        )
    )

    played = (
        stats.group_by(["player_id", "season"])
        .agg([
            pl.len().alias("games_played"),
            pl.col("team").last().alias("team"),
        ])
    )

    team_games = (
        stats.group_by(["team", "season"])
        .agg(pl.col("week").n_unique().alias("team_games"))
    )

    return (
        played.join(team_games, on=["team", "season"], how="left")
        # Both counts come back as UInt32. A player who changed teams can log
        # more games than any single team played, and unsigned subtraction
        # underflows to 2**32 instead of going negative. Cast first, clip after.
        .with_columns([
            pl.col("games_played").cast(pl.Int32),
            pl.col("team_games").cast(pl.Int32),
        ])
        .with_columns([
            (pl.col("team_games") - pl.col("games_played"))
            .clip(0, None)
            .alias("games_missed"),
        ])
        .with_columns(
            (pl.col("games_missed") / pl.col("team_games")).alias("missed_rate")
        )
    )


def build_features(seasons: list[int]) -> pl.DataFrame:
    """Injury features for a player-season, ready to lag like everything else.

    Emitted at the *observed* season; `features.build` shifts it forward so a
    row predicting season N carries season N-1's injury history.
    """
    avail = availability_panel(seasons)
    inj = injury_panel(seasons)

    df = avail.join(inj, on=["player_id", "season"], how="left").with_columns([
        pl.col("injury_report_weeks").fill_null(0),
        pl.col("weeks_listed_out").fill_null(0),
        pl.col("weeks_dnp").fill_null(0),
        pl.col("weeks_limited").fill_null(0),
        pl.col("weeks_soft_tissue").fill_null(0),
        pl.col("weeks_structural").fill_null(0),
        pl.col("distinct_injuries").fill_null(0),
    ])

    # Two-season lookback: the base rate persists, and two years of misses is
    # a much stronger signal than one.
    #
    # THE RECURRENCE FLAG USED TO LIVE HERE and it is gone; see MEASURED at the
    # top of this file. It said soft tissue in back-to-back seasons, it was the
    # feature this module expected most from, and once it could fire at all it
    # pointed the wrong way.
    prev = (
        df.select(["player_id", "season", "games_missed"])
        .with_columns((pl.col("season") + 1).alias("season"))
        .rename({"games_missed": "games_missed_prev"})
    )

    return df.join(prev, on=["player_id", "season"], how="left").with_columns([
        pl.col("games_missed_prev").fill_null(0),
        (pl.col("games_missed") + pl.col("games_missed_prev"))
        .alias("games_missed_2yr"),
    ])


def baseline_expectation(df: pl.DataFrame) -> pl.DataFrame:
    """Naive baseline: mean games missed by position and age bucket.

    Any availability model must beat this to be worth having. Report both.
    """
    if "age" not in df.columns:
        return df.group_by("position").agg(
            pl.col("games_missed").mean().round(3).alias("baseline_missed")
        )
    return (
        df.with_columns((pl.col("age") // 3 * 3).alias("age_bucket"))
        .group_by(["position", "age_bucket"])
        .agg([
            pl.len().alias("n"),
            pl.col("games_missed").mean().round(3).alias("baseline_missed"),
        ])
        .sort(["position", "age_bucket"])
    )


def persistence_check(df: pl.DataFrame) -> dict:
    """Does games-missed correlate year over year? The go/no-go test.

    Run this before building the availability model. A near-zero correlation
    means injury history carries almost no signal and Pillar 2's availability
    half should be scoped down to position/age base rates and stated plainly.
    """
    paired = df.filter(
        pl.col("games_missed").is_not_null()
        & pl.col("games_missed_prev").is_not_null()
        & (pl.col("games_missed_prev") > 0).or_(pl.col("games_missed") > 0)
    )
    if paired.height < 50:
        return {"error": "not enough paired seasons"}

    r = paired.select(
        pl.corr("games_missed", "games_missed_prev")
    ).item()

    return {
        "paired_seasons": paired.height,
        "year_over_year_r": round(r, 4) if r is not None else None,
        "reading": (
            "r>=0.4 strong, build it | 0.2-0.4 weak but usable | "
            "<0.15 mostly noise, use position/age base rates instead"
        ),
    }
