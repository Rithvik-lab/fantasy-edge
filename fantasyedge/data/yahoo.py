"""Yahoo ADP — the price a Yahoo draft room actually pays.

WHY A SECOND PLATFORM AT ALL

The whole reason ESPN replaced FantasyPros as the price source is that people
draft off the board in front of them, and boards disagree. That argument does
not stop at ESPN: a Yahoo league drafts from Yahoo's numbers, and using ESPN's
ADP there would repeat exactly the mistake this project already made once.

WHERE THIS COMES FROM

Yahoo runs a public read-only mirror of its fantasy API. No OAuth, no key:

    https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2
        /game/nfl/players;start=N;count=25;out=draft_analysis?format=json

`draft_analysis` carries `average_pick`, which is the ADP, plus
`percent_drafted` and an auction `average_cost`. The `preseason_*` variants are
the frozen pre-draft numbers; the plain ones move as the season's drafts
happen, so the plain ones are what a live draft should price against.

This is worth stating because the obvious route does not work: the public
draft-analysis WEB page renders client-side and contains no table at all, so
scraping it returns nothing. The API is the only fetchable path.

Yahoo pages at 25 players per request, so a full board is ~15 round trips.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from datetime import date

import polars as pl

from fantasyedge import config
from fantasyedge.data import crosswalk as cw

BASE = ("https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2/game/nfl/players"
        ";start={start};count={count};sort=AR;out=draft_analysis?format=json")

PAGE = 25          # Yahoo caps a page here regardless of what you ask for
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}


def _name_key(col: str) -> pl.Expr:
    """Normalised name, for matching across two id systems."""
    return (
        pl.col(col).str.to_lowercase()
        .str.replace_all(r"\b(jr|sr|ii|iii|iv|v)\.?$", "")
        .str.replace_all(r"[^a-z ]", "")
        .str.strip_chars()
    )


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _page(start: int, count: int = PAGE, timeout: int = 40) -> list[dict]:
    """One page of players with their draft analysis attached."""
    url = BASE.format(start=start, count=count)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=_context()) as fh:
        payload = json.load(fh)

    game = payload.get("fantasy_content", {}).get("game")
    if not isinstance(game, list) or len(game) < 2:
        return []
    players = game[1].get("players") or {}

    out = []
    for key, val in players.items():
        if key == "count" or not isinstance(val, dict):
            continue
        p = val.get("player")
        if not p:
            continue

        # Yahoo returns the metadata as a list of single-key dicts.
        meta: dict = {}
        head = p[0] if isinstance(p[0], list) else []
        for chunk in head:
            if isinstance(chunk, dict):
                meta.update(chunk)

        da_list = next(
            (x.get("draft_analysis") for x in p[1:]
             if isinstance(x, dict) and "draft_analysis" in x),
            None,
        )
        da: dict = {}
        for chunk in (da_list or []):
            if isinstance(chunk, dict):
                da.update(chunk)

        adp = da.get("average_pick") or da.get("preseason_average_pick")
        try:
            adp = float(adp)
        except (TypeError, ValueError):
            continue
        if adp <= 0:
            continue

        name = (meta.get("name") or {}).get("full")
        pos = meta.get("display_position") or meta.get("primary_position")
        # Yahoo writes multi-eligibility as "RB,WR"; the first is the real one.
        pos = (pos or "").split(",")[0].strip()
        if pos not in config.MODELED_POSITIONS and pos not in ("K", "DEF"):
            continue

        out.append({
            "yahoo_id": str(meta.get("player_id")),
            "market_name": name,
            "position": "DST" if pos == "DEF" else pos,
            "adp": adp,
            "percent_drafted": float(da.get("percent_drafted") or 0.0),
            "auction_value": float(da.get("average_cost") or 0.0),
        })
    return out


def fetch(limit: int = 600) -> pl.DataFrame:
    """Yahoo ADP for the top `limit` players, joined to gsis_id."""
    rows: list[dict] = []
    start = 0
    while start < limit:
        try:
            page = _page(start, min(PAGE, limit - start))
        except urllib.error.HTTPError as exc:
            if start == 0:
                raise RuntimeError(f"Yahoo returned HTTP {exc.code}") from exc
            break          # partial board beats no board
        if not page:
            break
        rows.extend(page)
        start += PAGE

    if not rows:
        raise RuntimeError("Yahoo returned no players with an ADP")

    df = pl.DataFrame(rows).unique(subset=["yahoo_id"]).sort("adp")

    x = cw.load().select(["gsis_id", "yahoo_id", "name", "position"])
    by_id = (
        x.filter(pl.col("yahoo_id").is_not_null())
        .with_columns(pl.col("yahoo_id").cast(pl.Utf8))
        .unique(subset=["yahoo_id"])
        .select(["gsis_id", "yahoo_id"])
    )
    df = df.join(by_id, on="yahoo_id", how="left")

    # Fall back to the name for anyone the id map misses. The crosswalk's
    # yahoo_id lags a draft class, so without this every notable rookie --
    # Jeanty, Hampton, Love, McMillan -- drops off the board entirely.
    by_name = (
        x.with_columns(_name_key("name").alias("_k"))
        .unique(subset=["_k", "position"])
        .select([pl.col("gsis_id").alias("_g"), "_k", "position"])
    )
    df = (
        df.with_columns(_name_key("market_name").alias("_k"))
        .join(by_name, on=["_k", "position"], how="left")
        .with_columns(pl.coalesce("gsis_id", "_g").alias("gsis_id"))
        .drop("_k", "_g")
    )

    return (
        df.with_columns([
            # Float64 throughout: rank() is UInt32 and subtracting two of them
            # underflows. This has bitten this project four times.
            pl.col("adp").rank("ordinal").cast(pl.Float64).alias("ecr"),
            pl.col("adp").rank("ordinal").over("position").cast(pl.Int32)
              .alias("pos_rank"),
            # Yahoo publishes no per-player dispersion either, so use the same
            # widening rule as ESPN: deeper picks vary more.
            (2.0 + pl.col("adp") * 0.12).alias("sd"),
            pl.lit(str(date.today())).alias("scraped"),
        ])
        .sort("adp")
    )


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
    df.write_parquet(config.PROCESSED / "yahoo_adp_latest.parquet")
    df.write_parquet(config.PROCESSED / f"yahoo_adp_{date.today().isoformat()}.parquet")


def load() -> pl.DataFrame:
    path = config.PROCESSED / "yahoo_adp_latest.parquet"
    if not path.exists():
        raise FileNotFoundError("no Yahoo snapshot; run fetch() first")
    return pl.read_parquet(path)
