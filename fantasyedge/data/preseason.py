"""Preseason box scores -- the only evidence that exists about the backups.

WHY THIS IS NOT ALREADY IN THE WEEKLY PULL

nflverse publishes the regular season and the playoffs and nothing else.
`player_stats` for 2025 is 18,539 REG rows and 882 POST rows, and not one
preseason snap; `load_snap_counts` refuses a season outside 2012-2025 outright;
`load_schedules` for 2025 returns 285 games, which is 272 regular plus the
thirteen playoff games. So the six weeks of August -- the only football in
which the men behind the starters are the ones on the field -- are absent from
every source this app reads, and the weekly pull could not have found them.

ESPN has them. Its public scoreboard takes `seasontype=1` and returns 49 games
a year back through the four-game era (65 a year through 2019), and every
finished one carries a full box score and play-by-play.

WHAT IS AND IS NOT TRUSTWORTHY IN HERE

Preseason yardage on its own is close to meaningless, and for the familiar
reason: a fourth-quarter run in week two is carried by men who will be cut in
ten days, against men who will be cut in ten days. Ranking backups on August
production is the same mistake as ranking them on realised finish -- the number
is real and the context is doing all the work.

So the number this module cares most about is not what he did, it is WHEN he
was on the field. Preseason has a structure the regular season does not: the
first team plays a series or two and comes off, together, and everything after
that is a different league. A carry taken while the starting quarterback is
still in the huddle is evidence about the depth chart. A carry in the fourth
quarter is evidence about roster spot 53.

ESPN's own `starter` flag would answer this directly and does not work: in
preseason it is False for all 94 roster entries of every game checked, on both
sides. The workable substitute is the play-by-play. Find the last play the
team's first quarterback appears in; every play up to there was taken with the
ones. Measured on DET at CIN, 2026 preseason week 2: Burrow appears in 7 plays,
all in the first quarter, and stops; Joshua Dobbs plays 12, all first half;
Luke Altmyer plays 29, thirteen of them in the first half. That is the shape
this is reading, and it is visible in every game.

Play text names players as `J.Saylors`, so attribution is a first-initial join
against the box score's own roster for that game -- which matched 100 of 100
athletes on the game above. It is scoped to one team's athletes at a time, so
the only way to mis-attribute is two men on the SAME team sharing an initial
and a surname. That happens; it is rare, and it costs one player's count rather
than corrupting the table.

`plays` IS NOT A SNAP COUNT and must not be read as one. The play-by-play names
the men who touched the ball, threw it, or were flagged -- so for a receiver
this counts targets, and a man who ran thirty routes for one target counts one.
It is involvement, not participation. Nothing publishes preseason snap counts;
`load_snap_counts` will not even accept the season. Involvement is what there
is, and the ratio it is used for -- how much of his August came against the
first team -- is the part that survives the distinction.

MEASURED, 2016-2025 (no 2020 preseason), 4,507 backup player-seasons
────────────────────────────────────────────────────────────────────

`scripts/measure_preseason_signal.py`. Population: every RB/WR/TE who touched
the ball in a preseason game and was NOT his team's first-teamer on the week-one
depth chart. Label: his actual PPR points that season, and whether he finished
startable in a 12-team league. Nobody dropped for being cut or hurt -- a backup
who never played scores zero and stays in the sample.

    spearman with regular-season points, among backups

        first-team involvement                        0.382
        share of his team's first-team touches        0.396
        preseason touches                             0.175
        RAW PRESEASON POINTS                          0.159

RAW PRESEASON POINTS ARE WORSE THAN USELESS. Held inside depth rank 2, where
the comparison is fair, the ranking INVERTS:

        least August points  (Q1)   62.9 pts    9.4% startable
        most August points   (Q4)   55.8 pts    6.0% startable

Which is the trap this file was written around, arriving on schedule: the man
who piles up preseason yardage is the man still on the field in the fourth
quarter, and being on the field in the fourth quarter is what being fourth on
the depth chart looks like. The number is real and the context is the whole
story. It is the same shape as the soft-tissue flag in `features/injury`, which
also "worked" and also pointed backwards.

WHEN THE STARTER ACTUALLY WENT DOWN -- 1,965 cases where the week-one starter
at that position missed four or more games:

        no first-team August work    n=1431    15.6 pts    1.7% startable
        any first-team August work   n= 534    52.2 pts    7.9% startable

and among the men who had some, graded by share of their own team's first-team
touches, it is monotone:

        Q1   33.2 pts    4.5% startable
        Q2   39.0 pts    4.5%
        Q3   54.0 pts    8.2%
        Q4   82.7 pts   14.3%

For most backups this is one bit rather than a scale -- 3,326 of 4,507 never
took a snap with the ones and are indistinguishable from each other. The bit is
worth having anyway: at depth rank 2 it separates 47.1 points from 71.8, and a
5.3% chance of a startable season from 10.4%.

WHAT THIS DOES NOT CLAIM

Nothing here is wired into a valuation yet. The measurement says it deserves to
be; what it should be worth in points is a separate decision.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.request
from pathlib import Path

import polars as pl

from fantasyedge import config

SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl"
              "/scoreboard?dates={season}&seasontype=1&week={week}")
SUMMARY = ("https://site.api.espn.com/apis/site/v2/sports/football/nfl"
           "/summary?event={event}")

# Preseason has been four weeks since 2021 and five before it (the Hall of Fame
# game is its own week). Asking for a week that does not exist returns an empty
# event list, so the extra request costs nothing and the constant does not have
# to know which era it is in.
WEEKS = (1, 2, 3, 4, 5)

# Suffixes are dropped from a name before abbreviating: the play-by-play writes
# `M.Harrison`, not `M.HarrisonJr.`.
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Full PPR. Duplicated from nowhere -- the box score is ESPN's own totals, not
# nflverse's, so this cannot borrow a column that is already scored.
SCORING = {
    "pass_yards": 0.04, "pass_td": 4.0, "interceptions": -2.0,
    "rush_yards": 0.1, "rush_td": 6.0,
    "receptions": 1.0, "rec_yards": 0.1, "rec_td": 6.0,
    "fumbles_lost": -2.0,
}

# ESPN labels the box score columns rather than naming them, and the labels
# have moved between seasons (2026 dropped QBR from the passing group). Reading
# by label instead of by position survives that.
LABELS: dict[str, dict[str, str]] = {
    "passing": {"YDS": "pass_yards", "TD": "pass_td", "INT": "interceptions",
                "SACKS": "sacked"},
    "rushing": {"CAR": "rush_att", "YDS": "rush_yards", "TD": "rush_td"},
    "receiving": {"REC": "receptions", "YDS": "rec_yards", "TD": "rec_td",
                  "TGTS": "targets"},
    "fumbles": {"LOST": "fumbles_lost"},
    "kicking": {"PTS": "kick_points", "LONG": "fg_long"},
}
# Written `made/attempted` in one cell.
SPLIT: dict[str, dict[str, tuple[str, str]]] = {
    "passing": {"C/ATT": ("completions", "pass_att")},
    "kicking": {"FG": ("fg_made", "fg_att"), "XP": ("xp_made", "xp_att")},
}

NUMERIC = sorted(
    {v for m in LABELS.values() for v in m.values()}
    | {v for m in SPLIT.values() for pair in m.values() for v in pair}
)


def _context() -> ssl.SSLContext:
    """macOS Python often ships without a usable CA bundle."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_CTX = _context()


def _get(url: str, tries: int = 3) -> dict | None:
    for n in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
                return json.load(r)
        except Exception:
            if n == tries - 1:
                return None
            time.sleep(1.5 * (n + 1))
    return None


def _num(s: str | None) -> float:
    if s is None:
        return 0.0
    t = str(s).strip().replace(",", "")
    try:
        return float(t)
    except ValueError:
        return 0.0


def _abbrev(name: str) -> str | None:
    """`Jacob Saylors` -> `j.saylors`, the form the play-by-play writes."""
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    parts = [p for p in parts if p.rstrip(".").lower() not in SUFFIXES] or parts
    if len(parts) < 2:
        return None
    return (parts[0][0] + "." + "".join(parts[1:])).lower()


# ---------------------------------------------------------------------------
# the schedule
# ---------------------------------------------------------------------------

def schedule(season: int) -> pl.DataFrame:
    """Every preseason game ESPN knows about, finished or not."""
    rows = []
    for wk in WEEKS:
        d = _get(SCOREBOARD.format(season=season, week=wk))
        for ev in (d or {}).get("events", []):
            try:
                comp = ev["competitions"][0]
                home = next(c for c in comp["competitors"]
                            if c.get("homeAway") == "home")
                away = next(c for c in comp["competitors"]
                            if c.get("homeAway") == "away")
                rows.append({
                    "season": season,
                    "week": wk,
                    "game_id": str(ev["id"]),
                    "date": str(ev.get("date", ""))[:10],
                    "home": home["team"]["abbreviation"],
                    "away": away["team"]["abbreviation"],
                    "final": comp["status"]["type"]["name"] == "STATUS_FINAL",
                })
            except (KeyError, StopIteration):
                continue
    if not rows:
        return pl.DataFrame(schema={
            "season": pl.Int32, "week": pl.Int32, "game_id": pl.String,
            "date": pl.String, "home": pl.String, "away": pl.String,
            "final": pl.Boolean})
    return pl.DataFrame(rows).with_columns([
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32),
    ]).unique(subset=["game_id"]).sort(["week", "date"])


# ---------------------------------------------------------------------------
# one game
# ---------------------------------------------------------------------------

def _stat_rows(payload: dict) -> dict[tuple[str, str], dict]:
    """Box score, keyed by (team, espn player id)."""
    out: dict[tuple[str, str], dict] = {}
    for side in payload.get("boxscore", {}).get("players", []):
        team = side.get("team", {}).get("abbreviation")
        if not team:
            continue
        for group in side.get("statistics", []):
            gname = group.get("name")
            labels = group.get("labels") or []
            direct = LABELS.get(gname, {})
            split = SPLIT.get(gname, {})
            if not direct and not split:
                continue
            for a in group.get("athletes", []):
                ath = a.get("athlete") or {}
                pid = str(ath.get("id") or "")
                if not pid:
                    continue
                # No position here. The box score athlete carries id, name and
                # jersey and nothing else, so position is attached later from
                # the player master rather than guessed from which stat group
                # he turned up in -- that guess cannot tell a WR from a TE.
                out.setdefault((team, pid), {
                    "team": team, "espn_id": pid,
                    "player": ath.get("displayName"),
                })
                row = out[(team, pid)]
                stats = a.get("stats") or []
                for i, label in enumerate(labels):
                    if i >= len(stats):
                        break
                    if label in direct:
                        row[direct[label]] = _num(stats[i])
                    elif label in split:
                        made, att = split[label]
                        bits = str(stats[i]).split("/")
                        row[made] = _num(bits[0] if bits else None)
                        row[att] = _num(bits[1] if len(bits) > 1 else None)
    return out


def _snaps(payload: dict, roster: dict[str, list[tuple[str, str]]]
           ) -> dict[tuple[str, str], dict]:
    """Plays each man appears in, and how many came while the ones were in.

    `roster` maps team -> [(espn_id, abbreviated name)]. Matching is scoped to
    one team so that the opponent's identically-abbreviated player cannot steal
    a play.
    """
    plays = []
    for drive in (payload.get("drives") or {}).get("previous", []):
        off = (drive.get("team") or {}).get("abbreviation")
        for p in drive.get("plays", []):
            try:
                seq = int(p.get("sequenceNumber") or 0)
            except (TypeError, ValueError):
                seq = 0
            plays.append((seq,
                          (p.get("text") or "").replace(" ", "").lower(),
                          ((p.get("period") or {}).get("number") or 0),
                          off))
    plays.sort(key=lambda x: x[0])

    hits: dict[tuple[str, str], list[int]] = {}
    for idx, (_seq, text, period, _off) in enumerate(plays):
        for team, people in roster.items():
            if team.startswith("__qb__"):
                continue
            for pid, key in people:
                if key and key in text:
                    hits.setdefault((team, pid), []).append(idx)

    # The ones came off when the first quarterback did. "First" is whoever
    # threw earliest, not whoever is listed first -- ESPN's ordering is not
    # depth order and a team that opens with its backup should be read that way.
    out: dict[tuple[str, str], dict] = {}
    for team, people in roster.items():
        if team.startswith("__qb__"):
            continue
        # Only men who actually threw are candidates. The box score is the only
        # thing that knows who is a quarterback, so the caller marks them.
        pass_ids = {pid for pid, _ in roster.get(f"__qb__{team}", [])}
        window, ones_qb = -1, None
        thrown = [(idxs[0], pid, idxs) for pid in pass_ids
                  for idxs in [hits.get((team, pid), [])] if idxs]
        if thrown:
            _first, ones_qb, idxs = min(thrown, key=lambda x: x[0])
            window = max(idxs)
        for pid, _key in people:
            idxs = hits.get((team, pid))
            if not idxs:
                continue
            periods = [plays[i][2] for i in idxs]
            out[(team, pid)] = {
                "plays": len(idxs),
                "plays_with_ones": sum(1 for i in idxs if i <= window),
                "plays_first_half": sum(1 for p in periods if 1 <= p <= 2),
                "first_play": idxs[0],
                "ones_qb": ones_qb,
                "ones_window": window,
            }
    return out


def game(game_id: str) -> list[dict]:
    """One preseason game as player rows: the box score plus when he played."""
    payload = _get(SUMMARY.format(event=game_id))
    if not payload:
        return []

    stats = _stat_rows(payload)
    if not stats:
        return []

    roster: dict[str, list[tuple[str, str]]] = {}
    for (team, pid), row in stats.items():
        key = _abbrev(row.get("player") or "")
        if key:
            roster.setdefault(team, []).append((pid, key))
    # Who threw a pass, per team -- the only reliable way to find the man whose
    # exit ends the first-team snaps.
    for (team, pid), row in stats.items():
        if row.get("pass_att", 0) or row.get("completions", 0):
            roster.setdefault(f"__qb__{team}", []).append((pid, ""))

    snaps = _snaps(payload, roster)

    header = payload.get("header", {})
    comps = (header.get("competitions") or [{}])[0]
    sides = {c.get("team", {}).get("abbreviation"): c.get("homeAway")
             for c in comps.get("competitors", [])}
    date = str(comps.get("date") or "")[:10]
    week = ((header.get("week") if isinstance(header.get("week"), int)
             else None) or 0)
    season = ((header.get("season") or {}).get("year")
              or config.PRODUCTION_TARGET_SEASON)
    teams = [t for t in sides if t]
    opp = {t: next((o for o in teams if o != t), None) for t in teams}

    rows = []
    for (team, pid), row in stats.items():
        r = dict(row)
        r.update({"season": int(season), "week": int(week),
                  "game_id": str(game_id), "date": date,
                  "opp": opp.get(team), "home": sides.get(team) == "home"})
        r.update(snaps.get((team, pid), {
            "plays": 0, "plays_with_ones": 0, "plays_first_half": 0,
            "first_play": None, "ones_qb": None, "ones_window": -1}))
        rows.append(r)
    return rows


# ---------------------------------------------------------------------------
# a season
# ---------------------------------------------------------------------------

def _out(season: int) -> Path:
    return config.PROCESSED / f"preseason_{season}.parquet"


def _frame(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows, infer_schema_length=None)
    for c in NUMERIC:
        if c not in df.columns:
            df = df.with_columns(pl.lit(0.0).alias(c))
    df = df.with_columns([pl.col(c).cast(pl.Float64).fill_null(0.0)
                          for c in NUMERIC])
    pts = pl.lit(0.0)
    for col, w in SCORING.items():
        pts = pts + pl.col(col) * w
    return df.with_columns([
        pts.round(2).alias("fantasy_points_ppr"),
        (pl.col("rush_att") + pl.col("receptions")).alias("touches"),
        pl.col("espn_id").cast(pl.String),
        pl.col("ones_qb").cast(pl.String),
    ])


_QB_SCHEMA = {"ones_qb": pl.String, "ones_rank": pl.Int32}


def _qb_new(d: pl.DataFrame) -> pl.DataFrame:
    """The 2025-and-later chart: dated snapshots, ESPN ids, `pos_rank`."""
    q = d.filter(pl.col("pos_abb") == "QB")
    if not q.height:
        return pl.DataFrame(schema=_QB_SCHEMA)
    return (q.filter(pl.col("dt") == q["dt"].max())
             .select([pl.col("espn_id").cast(pl.String).alias("ones_qb"),
                      pl.col("pos_rank").cast(pl.Int32).alias("ones_rank")])
             .drop_nulls())


def _qb_old(d: pl.DataFrame) -> pl.DataFrame:
    """The pre-2025 chart: weekly, gsis ids, `depth_team`, no preseason rows.

    Week one is the right snapshot even though it is nominally after August --
    it is published before anybody has played a down that counts, and it is the
    first chart of the year that exists.
    """
    if "week" in d.columns:
        d = d.filter(pl.col("week") == 1)
    if "game_type" in d.columns:
        d = d.filter(pl.col("game_type") == "REG")
    q = d.filter((pl.col("position") == "QB") & pl.col("gsis_id").is_not_null())
    if not q.height:
        return pl.DataFrame(schema=_QB_SCHEMA)
    q = (q.select([pl.col("gsis_id").cast(pl.String),
                   pl.col("depth_team").cast(pl.Int32, strict=False)
                     .alias("ones_rank")])
          .drop_nulls())
    try:
        import nflreadpy as nfl

        pm = nfl.load_players()
    except Exception:
        return pl.DataFrame(schema=_QB_SCHEMA)
    if not {"espn_id", "gsis_id"} <= set(pm.columns):
        return pl.DataFrame(schema=_QB_SCHEMA)
    ids = (pm.select([pl.col("gsis_id").cast(pl.String),
                      pl.col("espn_id").cast(pl.String).alias("ones_qb")])
             .drop_nulls().unique(subset=["gsis_id"]))
    return q.join(ids, on="gsis_id", how="inner").select(list(_QB_SCHEMA))


def qb_depth(season: int | None = None) -> pl.DataFrame:
    """Where each quarterback sits on his own depth chart.

    Keyed on ESPN id alone rather than team plus id. That is deliberate: an
    ESPN id is unique league-wide, whereas the two sources spell four clubs
    differently -- WSH against WAS, LAR against LA -- and an abbreviation
    mismatch does not raise, it silently drops the Rams and the Commanders out
    of the answer. A quarterback who changes teams inside a season keeps his
    best listed rank, which is a rounding error at the only position where one
    man takes every snap.

    The chart schema changed in 2025. Both are read: local file first, then
    nflverse, then the old weekly layout with gsis ids.
    """
    season = season or config.PRODUCTION_TARGET_SEASON
    d = pl.DataFrame()
    p = config.PROCESSED / f"depth_charts_{season}.parquet"
    if p.exists():
        try:
            d = pl.read_parquet(p)
        except Exception:
            d = pl.DataFrame()
    if not d.height:
        try:
            import nflreadpy as nfl

            d = nfl.load_depth_charts(seasons=[season])
        except Exception:
            return pl.DataFrame(schema=_QB_SCHEMA)

    cols = set(d.columns)
    if {"pos_abb", "pos_rank", "espn_id", "dt"} <= cols:
        out = _qb_new(d)
    elif {"position", "depth_team", "gsis_id"} <= cols:
        out = _qb_old(d)
    else:
        return pl.DataFrame(schema=_QB_SCHEMA)
    if not out.height:
        return pl.DataFrame(schema=_QB_SCHEMA)
    return (out.sort("ones_rank")
               .unique(subset=["ones_qb"], keep="first")
               .select(list(_QB_SCHEMA)))


def grade(df: pl.DataFrame, season: int | None = None) -> pl.DataFrame:
    """Whose ones were they?

    `with_ones_share` is only meaningful if the window was set by the actual
    first-choice quarterback, and often it is not -- a starter who sits out
    entirely hands the label to his backup, and then a running back who took
    every snap of a game played by the QB2 and QB3 reads as a perfect 1.000.
    That is the same shape of error as pricing a bench man at zero: the number
    looks emphatic and means something else.

    So the depth rank of the man who set the window rides along, and
    `plays_with_qb1` counts only the snaps where it was really the first team.
    """
    if not df.height or "ones_qb" not in df.columns:
        return df
    ranks = qb_depth(season)
    out = df.drop([c for c in ("ones_rank", "plays_with_qb1")
                   if c in df.columns])
    out = out.with_columns(pl.col("ones_qb").cast(pl.String))
    if ranks.height:
        out = out.join(ranks, on="ones_qb", how="left")
    else:
        out = out.with_columns(pl.lit(None, dtype=pl.Int32).alias("ones_rank"))
    return out.with_columns(
        pl.when(pl.col("ones_rank") == 1)
          .then(pl.col("plays_with_ones"))
          .otherwise(0).cast(pl.Int32).alias("plays_with_qb1"))


def pull(season: int | None = None, refetch: bool = False) -> pl.DataFrame:
    """Every finished preseason game this season, incrementally.

    A finished game never changes, so games already on disk are not refetched.
    That matters because this is 49 HTTP calls cold and none warm -- without it,
    a weekly sync would spend a minute re-reading August every week until
    January.
    """
    season = season or config.PRODUCTION_TARGET_SEASON
    sch = schedule(season)
    if not sch.height:
        return pl.DataFrame()

    have = pl.DataFrame()
    p = _out(season)
    if p.exists() and not refetch:
        try:
            have = pl.read_parquet(p)
        except Exception:
            have = pl.DataFrame()

    known = set(have["game_id"].to_list()) if have.height else set()
    todo = [g for g in sch.filter(pl.col("final"))["game_id"].to_list()
            if g not in known]

    rows: list[dict] = []
    for gid in todo:
        rows.extend(game(gid))
        time.sleep(0.2)

    fresh = _frame(rows)
    if not fresh.height:
        return have
    if have.height:
        cols = [c for c in have.columns if c in fresh.columns]
        fresh = pl.concat([have.select(cols), fresh.select(cols)], how="vertical")
    return fresh.unique(subset=["game_id", "espn_id"], keep="last")


def save(df: pl.DataFrame, season: int | None = None) -> None:
    season = season or config.PRODUCTION_TARGET_SEASON
    if df.height:
        config.PROCESSED.mkdir(parents=True, exist_ok=True)
        df.write_parquet(_out(season))


def load(season: int | None = None) -> pl.DataFrame:
    season = season or config.PRODUCTION_TARGET_SEASON
    p = _out(season)
    return pl.read_parquet(p) if p.exists() else pl.DataFrame()


def roles(season: int | None = None, df: pl.DataFrame | None = None,
          enrich: bool = True) -> pl.DataFrame:
    """One row a player: what he did in August and who he did it against.

    `with_ones_share` is the column that carries the information. Raw preseason
    points rank the men who played the most fourth quarters; this ranks the men
    the coaching staff put on the field while the starters were still out there.
    """
    d = load(season) if df is None else df
    if not d.height:
        return pl.DataFrame()
    d = grade(d, season)

    agg = (
        d.group_by(["espn_id", "player"])
        .agg([
            pl.col("team").last().alias("team"),
            pl.col("game_id").n_unique().cast(pl.Int32).alias("games"),
            pl.col("plays").sum().cast(pl.Int32).alias("plays"),
            pl.col("plays_with_ones").sum().cast(pl.Int32)
              .alias("plays_with_ones"),
            pl.col("plays_with_qb1").sum().cast(pl.Int32)
              .alias("plays_with_qb1"),
            pl.col("touches").sum().alias("touches"),
            pl.col("targets").sum().alias("targets"),
            pl.col("rush_att").sum().alias("rush_att"),
            pl.col("receptions").sum().alias("receptions"),
            pl.col("pass_att").sum().alias("pass_att"),
            pl.col("fantasy_points_ppr").sum().round(2).alias("points"),
        ])
    )
    agg = agg.with_columns([
        pl.when(pl.col("plays") > 0)
          .then(pl.col("plays_with_ones") / pl.col("plays"))
          .otherwise(None).round(3).alias("with_ones_share"),
        pl.when(pl.col("plays") > 0)
          .then(pl.col("plays_with_qb1") / pl.col("plays"))
          .otherwise(None).round(3).alias("qb1_share"),
        # THE ONE THAT MEASURED BEST -- see MEASURED at the top. Clubs differ
        # enormously in how much they play the starters in August, and some
        # quarterbacks take no preseason snap at all, so a raw count marks a
        # good backup down for his own team's caution. His share of the
        # first-team work that actually existed does not.
        pl.when(pl.col("plays_with_qb1").sum().over("team") > 0)
          .then(pl.col("plays_with_qb1")
                / pl.col("plays_with_qb1").sum().over("team"))
          .otherwise(None).round(4).alias("qb1_team_share"),
        pl.when(pl.col("games") > 0)
          .then((pl.col("points") / pl.col("games")).round(2))
          .otherwise(None).alias("points_per_game"),
    ]).sort("plays_with_qb1", descending=True)
    return to_gsis(agg) if enrich else agg


def to_gsis(df: pl.DataFrame) -> pl.DataFrame:
    """Attach nflverse id and position so this can join to anything else.

    The local crosswalk covers only fantasy-relevant players who exist today --
    3,345 of them. A preseason roster is 94 men a side and most of them will
    never be fantasy-relevant, so the master player table is the right source
    here even though it costs a download. It is also the only place position
    comes from: the box score does not carry one.
    """
    blank = [pl.lit(None, dtype=pl.String).alias("player_id"),
             pl.lit(None, dtype=pl.String).alias("position")]
    if not df.height or "espn_id" not in df.columns:
        return df
    try:
        import nflreadpy as nfl

        pm = nfl.load_players()
    except Exception:
        return df.with_columns(blank)

    if not {"espn_id", "gsis_id", "position"} <= set(pm.columns):
        return df.with_columns(blank)

    keep = (pm.select([pl.col("espn_id").cast(pl.String),
                       pl.col("gsis_id").cast(pl.String).alias("player_id"),
                       pl.col("position").cast(pl.String)])
              .filter(pl.col("espn_id").is_not_null())
              .unique(subset=["espn_id"]))
    out = df.drop([c for c in ("player_id", "position") if c in df.columns])
    return out.with_columns(pl.col("espn_id").cast(pl.String)).join(
        keep, on="espn_id", how="left")
