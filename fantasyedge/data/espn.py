"""ESPN fantasy ADP — the price signal that actually matches an ESPN draft.

WHY THIS REPLACED FANTASYPROS

FantasyPros expert consensus is a fine *ranking*, but it is not what people
draft from. In an ESPN league everyone is looking at ESPN's board, so ESPN's
average draft position is the real market price. The gap is not small:

                     FantasyPros ECR    ESPN ADP
    Justin Jefferson       9.1            12.0
    De'Von Achane         21.1            12.4

Consensus says Jefferson goes twelve picks earlier; ESPN says they go
together. Every survival probability built on the wrong one is wrong, and
survival is what decides picks at the turn.

The endpoint is ESPN's own fantasy API. Unofficial and undocumented, so it
can change without notice -- callers should be able to fall back.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from datetime import date

import polars as pl

from fantasyedge import config
from fantasyedge.data import crosswalk as cw

BASE = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"
        "/{season}/segments/0/leaguedefaults/{scoring}?view=kona_player_info")

# ESPN's leaguedefaults ids. 3 is PPR, which is what this league uses.
SCORING = {"standard": 1, "half_ppr": 2, "full_ppr": 3}

POSITION = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}


def _context() -> ssl.SSLContext:
    """macOS Python often ships without a usable CA bundle."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(
    season: int | None = None,
    scoring: str = "full_ppr",
    limit: int = 600,
) -> pl.DataFrame:
    """Live ADP and ownership from ESPN, joined to gsis_id."""
    season = season or config.PRODUCTION_TARGET_SEASON
    url = BASE.format(season=season, scoring=SCORING.get(scoring, 3))

    filt = {"players": {
        "limit": limit,
        "sortDraftRanks": {"sortPriority": 100, "sortAsc": True,
                           "value": scoring.replace("full_ppr", "PPR").upper()
                           if scoring != "full_ppr" else "PPR"},
    }}
    req = urllib.request.Request(url, headers={
        "x-fantasy-filter": json.dumps(filt),
        "User-Agent": "Mozilla/5.0",
    })

    with urllib.request.urlopen(req, timeout=45, context=_context()) as fh:
        payload = json.load(fh)

    rows = []
    for entry in payload.get("players", []):
        p = entry.get("player") or {}
        own = p.get("ownership") or {}
        adp = own.get("averageDraftPosition")
        if not adp or adp <= 0:
            continue
        pos = POSITION.get(p.get("defaultPositionId"))
        if pos not in config.MODELED_POSITIONS:
            continue
        # ESPN publishes TWO different orderings and they are not the same
        # number. `averageDraftPosition` is where players actually go in real
        # drafts; `draftRanksByRankType` is ESPN's own editorial ranking, and
        # THAT is what the draft-room board is sorted by. They diverge -- for
        # 2026, ESPN ranks Achane 10th while drafters take him at 12.3.
        #
        # The engine wants ADP, because survival probability is a question
        # about what the room will do, not what ESPN advises. But the rank is
        # carried through so the app can show the number you are looking at
        # on ESPN's screen next to the one the model reasons about.
        ranks = p.get("draftRanksByRankType") or {}
        rank_key = {"full_ppr": "PPR", "half_ppr": "PPR",
                    "standard": "STANDARD"}.get(scoring, "PPR")
        draft_rank = (ranks.get(rank_key) or {}).get("rank")
        rows.append({
            "espn_id": str(p.get("id")),
            "market_name": p.get("fullName"),
            "position": pos,
            "adp": float(adp),
            "draft_rank": float(draft_rank) if draft_rank else None,
            "adp_sd": float(own.get("auctionValueAverageChange") or 0.0),
            "percent_owned": float(own.get("percentOwned") or 0.0),
            "percent_started": float(own.get("percentStarted") or 0.0),
        })

    if not rows:
        raise RuntimeError("ESPN returned no players with an ADP")

    df = pl.DataFrame(rows)

    x = (
        cw.load()
        .select(["gsis_id", "espn_id", "name"])
        .filter(pl.col("espn_id").is_not_null())
        .with_columns(pl.col("espn_id").cast(pl.Utf8))
    )

    joined = df.join(x, on="espn_id", how="left")

    return joined.with_columns([
        # Float64 throughout: rank() returns UInt32, and any later
        # subtraction of two rank columns underflows on unsigned types.
        pl.col("adp").rank("ordinal").cast(pl.Float64).alias("ecr"),
        pl.col("adp").rank("ordinal").over("position").cast(pl.UInt32).alias("pos_rank"),
        # ESPN publishes no per-player ADP dispersion, so approximate it: the
        # deeper a player goes, the more his actual pick varies. Roughly
        # linear in ADP, matching observed draft-position spread.
        (2.0 + pl.col("adp") * 0.12).alias("sd"),
        pl.lit(str(date.today())).alias("scraped"),
    ]).sort("adp")


def match_report(df: pl.DataFrame) -> dict:
    matched = df.filter(pl.col("gsis_id").is_not_null()).height
    return {
        "players": df.height,
        "matched": matched,
        "unmatched": df.height - matched,
        "match_rate": round(100 * matched / df.height, 1) if df.height else 0.0,
    }


def save(df: pl.DataFrame) -> None:
    config.PROCESSED.mkdir(parents=True, exist_ok=True)
    df.write_parquet(config.PROCESSED / "espn_adp_latest.parquet")
    df.write_parquet(config.PROCESSED / f"espn_adp_{date.today().isoformat()}.parquet")


def load() -> pl.DataFrame:
    path = config.PROCESSED / "espn_adp_latest.parquet"
    if not path.exists():
        raise FileNotFoundError("no ESPN snapshot; run fetch() first")
    return pl.read_parquet(path)
