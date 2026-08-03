"""Live market price — FantasyPros expert consensus via DynastyProcess.

This is the "price" half of relative valuation. Model output is only
interesting relative to what the market charges.

FantasyPros publishes many ranking variants in one table. Two columns select
the one you want, and reading the wrong pair silently gives you dynasty or
superflex prices for a redraft PPR league.

`ecr_type` prefix is the league format -- NOT "draft":

    r*   redraft      <- us
    d*   DYNASTY      (values youth heavily; a 30-year-old stud ranks far
                       lower than his redraft value. wrong for this league.)
    b*   best ball

`page_type` selects overall vs positional:

    redraft-overall   <- the draft board (ecr_type "ro", 447 players)
    redraft-wr/rb/... positional boards (ecr_type "rp" for PPR)

Note the overall redraft board is published as a single consensus; a
PPR-specific *overall* page is not separately exposed. Positional PPR ranks
(`rp`) carry the PPR weighting, so use those for within-position value and
the overall board for draft order.

`sd`, `best`, and `worst` come along for free and are worth more than they
look: they measure *expert disagreement*. A player the experts agree on is
priced efficiently. A player they disagree wildly about is where mispricing
actually lives — which makes `sd` a genuine sleeper-hunting signal, not just
metadata.
"""

from __future__ import annotations

from datetime import date

import nflreadpy as nfl
import polars as pl

from fantasyedge import config
from fantasyedge.data import crosswalk as cw

REDRAFT_OVERALL = ("redraft-overall", "ro")
REDRAFT_PPR_POSITIONAL = (None, "rp")  # page varies by position


def fetch(
    page_type: str | None = "redraft-overall",
    ecr_type: str = "ro",
    rankings_type: str = "draft",
) -> pl.DataFrame:
    """Current consensus rankings, joined to gsis_id.

    Defaults to the redraft overall board — the draft-day price. Pass
    page_type=None, ecr_type="rp" for PPR positional ranks instead.
    """
    raw = nfl.load_ff_rankings(type=rankings_type)

    cond = (
        (pl.col("ecr_type") == ecr_type)
        & pl.col("pos").is_in(list(config.MODELED_POSITIONS))
        & pl.col("ecr").is_not_null()
    )
    if page_type is not None:
        cond = cond & (pl.col("page_type") == page_type)

    ranked = (
        raw.filter(cond)
        .select([
            pl.col("id").alias("fantasypros_id"),
            pl.col("player").alias("market_name"),
            pl.col("pos").alias("position"),
            pl.col("team").alias("market_team"),
            "ecr", "sd", "best", "worst",
            pl.col("scrape_date").alias("scraped"),
        ])
        # One row per player: the same player can appear under several pages.
        .sort("ecr")
        .unique(subset=["fantasypros_id"], keep="first")
    )

    x = (
        cw.load()
        .select(["gsis_id", "fantasypros_id", "name"])
        .filter(pl.col("fantasypros_id").is_not_null())
    )

    joined = ranked.join(x, on="fantasypros_id", how="left")

    return joined.with_columns([
        # Positional rank — what actually matters for replacement level.
        pl.col("ecr").rank("ordinal").over("position").alias("pos_rank"),
        # Expert disagreement, normalized. High = the market itself is unsure.
        pl.when(pl.col("ecr") > 0)
        .then(pl.col("sd") / pl.col("ecr"))
        .otherwise(None)
        .alias("market_uncertainty"),
    ]).sort("ecr")


def historical(
    seasons: list[int],
    page_type: str = "redraft-overall",
    month: int = 8,
) -> pl.DataFrame:
    """Preseason consensus for past seasons — the market price at draft time.

    Needed to ask the only question that matters: when the model disagreed
    with the market, who was right? Without a *contemporaneous* price that
    comparison is meaningless, so this pulls the August snapshot for each
    season rather than anything computed after the fact.

    The archive begins late 2019, so seasons before 2020 have no price.
    """
    raw = nfl.load_ff_rankings(type="all")

    dated = raw.with_columns(
        pl.col("scrape_date").cast(pl.Date, strict=False).alias("_d")
    ).filter(pl.col("_d").is_not_null())

    dated = dated.with_columns([
        pl.col("_d").dt.year().alias("_yr"),
        pl.col("_d").dt.month().alias("_mo"),
    ]).filter(
        pl.col("_yr").is_in(seasons)
        & (pl.col("_mo") == month)
        & (pl.col("page_type") == page_type)
        & pl.col("pos").is_in(list(config.MODELED_POSITIONS))
        & pl.col("ecr").is_not_null()
    )

    # Several scrapes per month; keep the latest before the season starts.
    latest = (
        dated.group_by("_yr").agg(pl.col("_d").max().alias("_d"))
    )
    snap = dated.join(latest, on=["_yr", "_d"], how="inner")

    x = (
        cw.load()
        .select(["gsis_id", "fantasypros_id"])
        .filter(pl.col("fantasypros_id").is_not_null())
    )

    return (
        snap.select([
            pl.col("_yr").alias("season"),
            # The archive types `id` as String; the current-season pull types
            # it Int64. Same column, same meaning, different dtype.
            pl.col("id").cast(pl.Int64, strict=False).alias("fantasypros_id"),
            pl.col("player").alias("market_name"),
            pl.col("pos").alias("position"),
            "ecr", "sd",
            pl.col("_d").alias("priced_on"),
        ])
        .sort("ecr")
        .unique(subset=["season", "fantasypros_id"], keep="first")
        .join(x, on="fantasypros_id", how="inner")
        .rename({"gsis_id": "player_id"})
        .with_columns(
            pl.col("ecr").rank("ordinal").over(["season", "position"])
            .alias("market_pos_rank")
        )
    )


def unmatched(df: pl.DataFrame) -> pl.DataFrame:
    """Ranked players we could not map to a gsis_id.

    These are silently missing from every downstream model, so look at them
    rather than trusting the join. Usually rookies who have not played a
    snap — which is expected, not a bug.
    """
    return df.filter(pl.col("gsis_id").is_null()).select(
        ["market_name", "position", "market_team", "ecr"]
    )


def match_report(df: pl.DataFrame) -> dict:
    matched = df.filter(pl.col("gsis_id").is_not_null()).height
    return {
        "ranked_players": df.height,
        "matched_to_gsis": matched,
        "unmatched": df.height - matched,
        "match_rate": round(100 * matched / df.height, 1) if df.height else 0.0,
    }


def save(df: pl.DataFrame, tag: str = "draft") -> "config.Path":
    """Snapshot to disk, dated. ECR moves daily in August."""
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    out = config.PROCESSED / f"market_{tag}_{date.today().isoformat()}.parquet"
    df.write_parquet(out)
    latest = config.PROCESSED / f"market_{tag}_latest.parquet"
    df.write_parquet(latest)
    return out


def load(tag: str = "draft") -> pl.DataFrame:
    path = config.PROCESSED / f"market_{tag}_latest.parquet"
    if not path.exists():
        raise FileNotFoundError("no market snapshot; run scripts/refresh_market.py")
    return pl.read_parquet(path)
