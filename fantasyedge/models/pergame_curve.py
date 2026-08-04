"""Per-game outcome curve, indexed by where a player was ranked BEFORE the season.

THE DEFECT THIS REPLACES

The draft board converts a market rank into a season distribution by looking
up a per-game curve: "the RB ranked 8th scores q20/q50/q80 per game and plays
c_games games". The curve shipped until now was built by grouping players on
the rank they FINISHED at.

That conditions on the outcome. Everyone who finished RB8 finished RB8 -- by
construction they neither busted nor got hurt, so the band around them is
tight and their availability is near-perfect:

    finished-rank curve, RB1:  q20 16.4  q50 23.3  q80 31.7  games 16.4

Sixteen and a half games is not a forecast, it is a description of what it
takes to finish first. Every floor the board printed inherited that, which is
why season floors read optimistic and why unproven players came out looking
safe -- the widening that should have come from bust risk was conditioned
away before the number was ever computed.

WHAT REPLACES IT

Group on an ex-ante ranking instead and ask what actually happened to those
players -- including the ones who busted, and the ones who played four games.
Two candidate anchors, since neither is the ADP the board actually uses:

    prior_rank   last season's positional finish. 20 seasons of coverage,
                 but a weaker ranking than ADP, which knows about holdouts,
                 injuries and depth-chart moves that a stale finish does not.
    model        the season model's walk-forward projection. Closer in
                 quality to a market price, but only the validation seasons
                 and only players with real prior usage.

A weaker anchor produces a wider band, so prior_rank is an upper bound and
model is a lower bound on the truth. `compare_anchors` reports both; the
production curve uses prior_rank, and the gap between the two is small next
to the gap between either of them and the finished-rank curve it replaces.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from fantasyedge import config

# A rate over one or two games is noise, not a scoring level. Availability is
# measured over EVERYONE though -- the zero-game seasons are the point.
MIN_GAMES_FOR_RATE = 4

# Cells are thin at any single rank, so pool neighbours. The window widens
# with rank because the curve flattens as it goes and deep ranks are noisier.
def _window(rank: int) -> int:
    return max(2, int(round(0.18 * rank)))


def _season_table(min_season: int = 2006) -> pl.DataFrame:
    """Season-grain outcomes with both candidate ex-ante anchors attached."""
    f = (
        pl.read_parquet(config.PROCESSED / "features.parquet")
        .filter(pl.col("position").is_in(config.MODELED_POSITIONS)
                & (pl.col("season") >= min_season))
    )
    return f.with_columns([
        pl.col("prior_total_points").rank("ordinal", descending=True)
        .over(["season", "position"]).cast(pl.Int32).alias("prior_rank"),
        pl.when(pl.col("games") > 0)
        .then(pl.col("total_points") / pl.col("games"))
        .otherwise(None).alias("ppg_realized"),
    ])


def _model_anchor(table: pl.DataFrame) -> pl.DataFrame:
    """Rank by the season model's walk-forward projection, where it exists."""
    from fantasyedge.models import train

    oof, _ = train.walk_forward(table, label="total_points")
    if not oof.height:
        return table.with_columns(
            pl.lit(None, dtype=pl.Int32).alias("model_rank"))
    ranked = oof.with_columns(
        pl.col("pred").rank("ordinal", descending=True)
        .over(["season", "position"]).cast(pl.Int32).alias("model_rank")
    ).select(["player_id", "season", "model_rank"])
    return table.join(ranked, on=["player_id", "season"], how="left")


def build(anchor: str = "prior_rank", min_season: int = 2006,
          max_rank: int | None = None) -> pl.DataFrame:
    """Per-game quantiles and expected availability, by pre-season rank."""
    table = _season_table(min_season)
    if anchor == "model":
        table, anchor = _model_anchor(table), "model_rank"
    if anchor not in table.columns:
        raise ValueError(f"unknown anchor {anchor!r}")

    table = table.filter(pl.col(anchor).is_not_null())
    rows = []
    for pos in sorted(table["position"].unique().to_list()):
        d = table.filter(pl.col("position") == pos)
        a = d[anchor].to_numpy()
        ppg = d["ppg_realized"].to_numpy()
        gms = d["games"].cast(pl.Float64).to_numpy()
        pts = d["total_points"].cast(pl.Float64).to_numpy()
        wsd = d["weekly_sd"].cast(pl.Float64).to_numpy()
        enough = d["games"].to_numpy() >= MIN_GAMES_FOR_RATE

        top = int(max_rank or a.max())
        for r in range(1, top + 1):
            w = _window(r)
            near = np.abs(a - r) <= w
            rate = ppg[near & enough]
            rate = rate[~np.isnan(rate)]
            avail = gms[near]
            if rate.size < 8 or avail.size < 8:
                continue
            rows.append({
                "position": pos,
                "pr": r,
                "c_q20": float(np.quantile(rate, 0.20)),
                "c_q50": float(np.quantile(rate, 0.50)),
                "c_q80": float(np.quantile(rate, 0.80)),
                # Availability over everyone ranked here, busts included.
                "c_games": float(avail.mean()),
                # Expected season total for someone ranked here, which is what
                # `projected_points` should be. The board used to read this off
                # the finished-rank curve too, so its projections carried the
                # same survivorship bias as its floors.
                "c_points": float(np.nanmean(pts[near])),
                # Within-player week-to-week spread, which the simulator
                # needs kept separate from the cross-sectional band above.
                "c_weekly_sd": float(np.nanmean(wsd[near & enough])),
                # The season band, measured rather than simulated. Rate and
                # availability are correlated -- a player losing his job
                # scores less AND plays less -- so composing them as if they
                # were independent understates the season spread. These are
                # the realised totals of everyone ranked here, busts and
                # zero-game seasons included, and they need no assumption.
                "c_season_p20": float(np.nanquantile(pts[near], 0.20)),
                "c_season_p50": float(np.nanquantile(pts[near], 0.50)),
                "c_season_p80": float(np.nanquantile(pts[near], 0.80)),
                "n": int(avail.size),
            })
    return pl.DataFrame(rows, schema_overrides={"pr": pl.Int32})


def save(curve: pl.DataFrame) -> None:
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    curve.write_parquet(config.PROCESSED / "pergame_curve.parquet")


def compare_anchors(ranks=(1, 3, 8, 15, 24, 36)) -> pl.DataFrame:
    """Old finished-rank curve against both ex-ante anchors, side by side."""
    old_path = config.PROCESSED / "pergame_curve_finished_rank.parquet"
    out = []
    for name, curve in (
        ("finished", pl.read_parquet(old_path) if old_path.exists() else None),
        ("prior_rank", build("prior_rank")),
        ("model", build("model")),
    ):
        if curve is None or not curve.height:
            continue
        d = curve.filter((pl.col("position") == "RB") & pl.col("pr").is_in(ranks))
        out.append(d.with_columns(pl.lit(name).alias("curve"),
                                  pl.col("n").cast(pl.Int64)))
    if not out:
        return pl.DataFrame()
    return (
        pl.concat(out)
        .with_columns([
            (pl.col("c_q80") - pl.col("c_q20")).round(2).alias("band"),
            pl.col("c_q50").round(2), pl.col("c_games").round(2),
        ])
        .select(["curve", "pr", "c_q20", "c_q50", "c_q80", "band", "c_games", "n"])
        .sort(["pr", "curve"])
    )


if __name__ == "__main__":
    print(compare_anchors())
    c = build("prior_rank")
    print(f"\nproduction curve: {c.height} rows, "
          f"{c['position'].n_unique()} positions, median cell n={c['n'].median():.0f}")
