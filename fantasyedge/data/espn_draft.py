"""Read a live ESPN draft as it happens.

WHY THIS EXISTS

Advice that arrives after the pick is worse than no advice. Typing every pick
into a terminal while you are on the clock costs the seconds that decide
whether a name is still there, and the failure mode is specific: you get told
to take a player who came off the board two picks ago.

ESPN's own league endpoint carries the draft as it fills in, so the board can
track itself. Polling it every couple of seconds means a recommendation is
always computed against the picks that have actually happened.

    GET /apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}
        ?view=mDraftDetail&view=mTeam&view=mSettings

Public leagues need nothing. Private leagues need the `espn_s2` and `SWID`
cookies from a logged-in browser session -- they go in .env, which is
gitignored, and never leave this machine.

The same payload also carries the league's real settings, so team count,
lineup and PPR come from the league itself rather than from someone typing
them in and getting one wrong.

Undocumented and unofficial: parse leniently and let callers fall back.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import polars as pl

from fantasyedge.data import crosswalk as cw

BASE = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"
        "/{season}/segments/0/leagues/{league}")
VIEWS = "?view=mDraftDetail&view=mTeam&view=mSettings&view=mRoster"

# ESPN lineup slot ids -> our position names. Anything not listed (bench, IR,
# individual defensive players) is deliberately ignored.
SLOTS = {
    0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "DST", 17: "K",
    23: "FLEX", 7: "SUPERFLEX",
}
BENCH_SLOTS = {20}
# Slot 21 is injured reserve. It arrives in the same map as the bench and was
# counted with it, which invented a roster spot: a 16+1 league read as 17 men
# of room. An IR seat only takes a player ESPN lists as unavailable.
IR_SLOT = 21

# statId 53 is receptions; its points value is the PPR setting.
RECEPTION_STAT = 53


class DraftUnavailable(RuntimeError):
    """ESPN would not give us the draft -- wrong id, private, or not started."""


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(league_id: str | int, season: int, espn_s2: str | None = None,
          swid: str | None = None, timeout: int = 20) -> dict:
    """Raw league payload including the draft so far."""
    url = BASE.format(season=season, league=league_id) + VIEWS
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if espn_s2 and swid:
        if not swid.startswith("{"):
            swid = "{" + swid.strip("{}") + "}"
        headers["Cookie"] = f"espn_s2={espn_s2}; SWID={swid}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_context()) as fh:
            return json.load(fh)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise DraftUnavailable(
                "ESPN refused the request. A private league needs espn_s2 and "
                "SWID cookies from a logged-in browser session."
            ) from exc
        if exc.code == 404:
            raise DraftUnavailable(
                f"No league {league_id} in season {season}."
            ) from exc
        raise DraftUnavailable(f"ESPN returned HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 - network shapes vary
        raise DraftUnavailable(f"could not reach ESPN: {exc}") from exc


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class LeagueInfo:
    """What ESPN says this league actually is."""

    name: str = "ESPN league"
    n_teams: int = 12
    points_per_reception: float = 1.0
    roster_size: int = 16
    ir_slots: int = 0
    lineup: dict[str, int] = field(default_factory=dict)
    teams: list[dict] = field(default_factory=list)
    in_progress: bool = False
    complete: bool = False
    draft_time: float | None = None   # epoch seconds


def league_info(payload: dict) -> LeagueInfo:
    """Team count, lineup and scoring, read off the league itself."""
    settings = payload.get("settings") or {}
    roster = (settings.get("rosterSettings") or {})
    counts = roster.get("lineupSlotCounts") or {}

    lineup: dict[str, int] = {}
    bench = 0
    ir = 0
    for slot, n in counts.items():
        n = int(n or 0)
        if not n:
            continue
        sid = int(slot)
        if sid in BENCH_SLOTS:
            bench += n
            continue
        if sid == IR_SLOT:
            ir += n
            continue
        name = SLOTS.get(sid)
        if name:
            lineup[name] = lineup.get(name, 0) + n

    ppr = 1.0
    for item in (settings.get("scoringSettings") or {}).get("scoringItems", []):
        if item.get("statId") == RECEPTION_STAT:
            ppr = float(item.get("points", 1.0))
            break

    teams = [
        {
            "id": t.get("id"),
            "name": (t.get("name")
                     or f"{t.get('location','')} {t.get('nickname','')}".strip()
                     or f"Team {t.get('id')}"),
            "abbrev": t.get("abbrev") or "",
        }
        for t in payload.get("teams") or []
    ]

    detail = payload.get("draftDetail") or {}
    # ESPN publishes the scheduled start in epoch MILLIseconds.
    raw = (settings.get("draftSettings") or {}).get("date")
    when = float(raw) / 1000.0 if raw else None
    starters = sum(lineup.values())
    return LeagueInfo(
        name=settings.get("name") or "ESPN league",
        n_teams=len(teams) or int(settings.get("size") or 12),
        points_per_reception=ppr,
        roster_size=(starters + bench) or 16,
        ir_slots=ir,
        lineup=lineup or {"QB": 1, "RB": 2, "WR": 2, "TE": 1,
                          "FLEX": 1, "K": 1, "DST": 1},
        teams=teams,
        in_progress=bool(detail.get("inProgress")),
        complete=bool(detail.get("drafted")),
        draft_time=when,
    )


def picks(payload: dict) -> pl.DataFrame:
    """Every pick made so far, in order, joined to our player ids.

    Returns overall/round/slot, the drafting team, and gsis_id where we can
    match it. An unmatched pick still counts as a pick -- the player is gone
    either way, and dropping him would leave him recommendable.
    """
    detail = payload.get("draftDetail") or {}
    rows = []
    for p in detail.get("picks") or []:
        rows.append({
            "overall": int(p.get("overallPickNumber") or 0),
            "round": int(p.get("roundId") or 0),
            "round_pick": int(p.get("roundPickNumber") or 0),
            "team_id": int(p.get("teamId") or 0),
            "espn_id": str(p.get("playerId")),
            "keeper": bool(p.get("keeper")),
        })
    if not rows:
        return pl.DataFrame(schema={
            "overall": pl.Int64, "round": pl.Int64, "round_pick": pl.Int64,
            "team_id": pl.Int64, "espn_id": pl.Utf8, "keeper": pl.Boolean,
            "gsis_id": pl.Utf8, "player_name": pl.Utf8,
        })

    df = pl.DataFrame(rows).filter(pl.col("overall") > 0).sort("overall")
    x = (
        cw.load().select(["gsis_id", "espn_id", "name"])
        .filter(pl.col("espn_id").is_not_null())
        .with_columns(pl.col("espn_id").cast(pl.Utf8))
        .unique(subset=["espn_id"])
    )
    return (
        df.join(x, on="espn_id", how="left")
        .rename({"name": "player_name"})
    )


def my_team_id(payload: dict, swid: str | None) -> int | None:
    """Which team is yours, from the SWID on the membership record."""
    if not swid:
        return None
    key = swid.strip("{}").lower()
    for m in payload.get("members") or []:
        if str(m.get("id", "")).strip("{}").lower() == key:
            break
    else:
        return None
    for t in payload.get("teams") or []:
        owners = [str(o).strip("{}").lower() for o in (t.get("owners") or [])]
        if key in owners:
            return int(t.get("id"))
    return None


def draft_slot(payload: dict, team_id: int | None) -> int | None:
    """Your position in the snake order, derived from round one."""
    if team_id is None:
        return None
    for p in (payload.get("draftDetail") or {}).get("picks") or []:
        if int(p.get("roundId") or 0) == 1 and int(p.get("teamId") or 0) == team_id:
            return int(p.get("roundPickNumber") or 0) or None
    # Fall back to ESPN's stated order if round one has not happened yet.
    order = (payload.get("settings") or {}).get("draftSettings", {}).get("pickOrder")
    if order and team_id in order:
        return order.index(team_id) + 1
    return None


# ---------------------------------------------------------------------------
# In-season rosters
# ---------------------------------------------------------------------------
# The payload has carried `mRoster` since the first version of this file and
# nothing ever read it -- everything came from `draftDetail.picks`, which is
# frozen the moment the draft ends. That is fine for draft day and useless
# after it: waivers, drops and trades all happen against rosters that the pick
# log cannot see. Trade mode needs who owns whom TODAY.

POSITION_BY_ID = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}


def rosters(payload: dict) -> pl.DataFrame:
    """Every team's CURRENT roster, as ESPN holds it right now.

    Returns one row per owned player: team_id, player_id (gsis where we can
    resolve it), name, position, and whether he is in a starting slot. Falls
    back to the ESPN id when the crosswalk cannot place him, so a player is
    never silently dropped from a roster -- an incomplete roster would quietly
    understate a trade.
    """
    rows = []
    for t in payload.get("teams") or []:
        tid = t.get("id")
        entries = ((t.get("roster") or {}).get("entries")) or []
        for e in entries:
            pool = e.get("playerPoolEntry") or {}
            p = pool.get("player") or e.get("player") or {}
            pid = p.get("id")
            if pid is None:
                continue
            slot = e.get("lineupSlotId")
            rows.append({
                "team_id": tid,
                "espn_id": str(pid),
                "player_name": p.get("fullName") or "",
                "position": POSITION_BY_ID.get(p.get("defaultPositionId"), None),
                "lineup_slot": SLOTS.get(slot),
                "starting": slot is not None and slot not in BENCH_SLOTS
                            and slot in SLOTS,
                "injury_status": p.get("injuryStatus"),
            })

    if not rows:
        return pl.DataFrame()

    df = pl.DataFrame(rows)
    xw = cw.load().select(["gsis_id", "espn_id"]).drop_nulls()
    if xw.height:
        xw = xw.with_columns(pl.col("espn_id").cast(pl.Utf8)).unique(subset=["espn_id"])
        df = df.join(xw, on="espn_id", how="left")
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("gsis_id"))

    # Keep the ESPN id when the crosswalk has no gsis for him. A roster with a
    # hole in it is worse than one with an id we cannot join on elsewhere.
    #
    # PREFIXED, because that is the key the board uses for exactly the same
    # players. Kickers and defences have no gsis and never will -- a defence is
    # a team, not a person -- so `models/kdst` keys them `espn-<id>`. Falling
    # back to the BARE id here produced a roster whose kicker and defence
    # matched nothing on the board, and they then vanished from My Team, from
    # every lineup, and from both sides of every trade. Same player, two
    # spellings of his id.
    return df.with_columns(
        pl.coalesce([pl.col("gsis_id"),
                     pl.lit("espn-") + pl.col("espn_id")]).alias("player_id")
    )
