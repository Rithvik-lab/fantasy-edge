"""HTTP layer over the draft engine.

Everything here is a thin wrapper. The engine decides who to take, this
decides how to say it over JSON. Keeping it thin is deliberate -- the command
surface mirrors draft.py, so both front ends stay honest about running the
same code.

    uvicorn server.app:app --reload --port 8000
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import polars as pl
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import draft as D
from fantasyedge import config
from fantasyedge.data import espn_draft, refresh
from fantasyedge import waiver
from fantasyedge.draft import explain, report
from fantasyedge.draft.engine import (
    DraftState,
    recommend,
    roster_needs,
    survival_probability,
)
from fantasyedge.draft.session import grade_roster, optimal_lineup
from fantasyedge import trade
from fantasyedge.league import (LeagueSettings, fits, picks_for_slot,
                                slot_for_pick)
from server import store

app = FastAPI(title="FantasyEdge draft", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADSHOT = "https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png"

# The commit this process was started from. A long-lived uvicorn silently
# serving month-old code is not a hypothetical -- one ran here for four days
# and answered 404 to every endpoint added in that time, which reads in the
# browser as "not found" and tells you nothing. The front end compares this
# against what it was built from and says so plainly.
def _build() -> str:
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=2,
                              cwd=Path(__file__).resolve().parents[1]
                              ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


BUILD = _build()
STARTED = time.time()


# ---------------------------------------------------------------------------
# ONE PROCESS, ONE DRAFT, NO USERS.
#
# `STATE` below is a single module-level object. There is no login, no session,
# and nothing about a draft is stored in the browser -- so every client that
# can reach this port sees the same draft. That is exactly right for the way
# this is meant to run: each person clones the repo and starts their own copy,
# and the only reason your league is private is that the socket does not listen
# off your machine.
#
# Which makes the binding a security control, not a convenience. `--host
# 0.0.0.0` would hand every visitor the same STATE: their browser opens YOUR
# draft, and /api/leagues lists YOUR saved leagues -- each of which can be
# loaded, and each of which carries the espn_s2 / SWID behind it. One flag,
# total exposure. So it is enforced here instead of being left to how the
# server happens to get started.
#
# Serving real multiple users means per-session state and an owner on every
# saved league. That is a different application; this refuses rather than
# pretending.
LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}
ALLOW_REMOTE = os.environ.get("FANTASYEDGE_ALLOW_REMOTE") == "1"


@app.middleware("http")
async def loopback_only(request: Request, call_next):
    host = request.client.host if request.client else None
    if not ALLOW_REMOTE and host not in LOOPBACK:
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "FantasyEdge holds one draft and has no login, so it only "
                    "answers this machine. Run your own copy. If you truly "
                    "want everyone who can reach this port to share a single "
                    "draft and a single set of ESPN credentials, set "
                    "FANTASYEDGE_ALLOW_REMOTE=1."
                )
            },
        )
    return await call_next(request)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class Draft:
    """One in-progress draft. Single-user by design; this runs on your laptop."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.settings: LeagueSettings | None = None
        self.my_slot: int = 1
        self.risk = "combined"
        self.bench_risk = "aggressive"
        self.picks: list[dict] = []          # {player_id, name, slot, overall}
        # Who owns whom TODAY, team_id -> board ids, straight off ESPN. The
        # pick log freezes when the draft ends and waivers, drops and trades
        # all happen after it, so once a draft is complete this is the truth
        # and the picks are only history.
        self.rosters: dict[int, list[str]] = {}
        # Your hand edits ON TOP of that roster, which have to survive a sync.
        # ESPN is authoritative and it is also SLOW -- a waiver claim shows up
        # there hours after you made it, and a trade only once both sides
        # accept. Without these, typing a name into your team did nothing
        # visible at all: the pick was recorded and then the next roster read
        # overwrote it.
        self.added: list[str] = []
        self.dropped: list[str] = []
        self.espn: dict | None = None        # league_id / season / cookies
        self.league_name = ""
        self.team_names: dict[int, str] = {}
        self.my_team_id: int | None = None
        self.last_sync: float = 0.0
        self.sync_error: str | None = None
        # ESPN does not publish the pick order until it locks, and before
        # round one there are no picks to infer it from. When neither is
        # available the slot is a GUESS, and a wrong seat quietly poisons
        # every survival probability -- so it gets flagged rather than
        # defaulted silently.
        self.slot_confirmed: bool = False
        # Whether the SWID matched a team in this league. None until we tried.
        # A mismatch fails the same way an unpublished pick order does -- seat
        # unknown -- but for an opposite reason and with an opposite fix, so
        # the two are kept apart rather than sharing one message.
        self.swid_matched: bool | None = None
        self.draft_started: bool = False
        self.draft_complete: bool = False
        self.draft_time: float | None = None    # epoch seconds, if ESPN says
        # Overall pick numbers you hold. None = the plain snake off my_slot.
        # People trade picks, and once they do "your picks" is a set of
        # numbers rather than a formula.
        self.owned_picks: list[int] | None = None
        # Players you have pinned into a starting slot by hand. The lineup is
        # computed optimally by default; this is the override for when you know
        # something the projection does not, and it is deliberately partial --
        # pin one man and the rest still solve around him.
        self.pinned: dict[str, str] = {}      # player_id -> slot
        self.pinned_week: int | None = None   # set by the week solver, if any
        self.platform: str = "espn"
        self.league_id: str | None = None      # set once saved
        self.name: str = ""

    # -- derived ----------------------------------------------------------

    @property
    def drafted_ids(self) -> list[str]:
        return [p["player_id"] for p in self.picks if p.get("player_id")]

    def my_picks(self) -> list[int]:
        """Overall pick numbers you hold, after any trades."""
        if self.owned_picks is not None:
            return sorted(self.owned_picks)
        return picks_for_slot(self.my_slot, self.settings.n_teams,
                              self.settings.n_rounds)

    @property
    def my_ids(self) -> list[str]:
        """Players you actually own.

        ESPN records which team made each pick, so once connected that is
        authoritative and handles trades for free. Off ESPN, ownership is
        whichever overall picks you say are yours.
        """
        # ONCE THE DRAFT IS OVER, THE ROSTER IS THE TRUTH. The pick log stops
        # the moment the draft does, so a waiver add is invisible to it and a
        # dropped man stays on your team forever. During the draft it is the
        # other way round -- picks land instantly, the roster view can lag --
        # so the switch happens exactly when the draft completes.
        #
        # Your own edits sit on top, because ESPN lags reality by hours and
        # sometimes by a whole trade negotiation. They are cleared the moment
        # ESPN agrees, so an add is a correction and never a second copy.
        if self.draft_complete and self.my_team_id in self.rosters:
            drop = set(self.dropped)
            live = [p for p in self.rosters[self.my_team_id] if p not in drop]
            return live + [p for p in self.added if p not in live]
        if self.my_team_id is not None:
            return [p["player_id"] for p in self.picks
                    if p.get("player_id") and p.get("team_id") == self.my_team_id]
        # A pick recorded by hand says outright whose it is. Only fall back to
        # the schedule for picks saved before that flag existed, or typed in
        # without one.
        flagged = [p for p in self.picks if "mine" in p]
        if flagged:
            return [p["player_id"] for p in flagged
                    if p.get("player_id") and p["mine"]]
        owned = set(self.my_picks())
        return [p["player_id"] for p in self.picks
                if p.get("player_id") and p.get("overall") in owned]

    def on_the_clock(self) -> tuple[int, int, int]:
        n = len(self.picks) + 1
        teams = self.settings.n_teams
        rnd = (n - 1) // teams + 1
        pick = (n - 1) % teams + 1
        return rnd, pick, n

    def is_my_turn(self) -> bool:
        _, _, overall = self.on_the_clock()
        return overall in set(self.my_picks())


STATE = Draft()


def _require() -> Draft:
    if STATE.settings is None:
        raise HTTPException(400, "no draft configured; POST /api/league first")
    return STATE


def _board() -> pl.DataFrame:
    return D.board(STATE.settings)


def _adopt(name: str) -> None:
    """Give this league a home on disk the moment it exists.

    Autosave keys off `league_id`, and that used to be set only by pressing
    Save -- so a freshly connected league was live in memory and invisible in
    My Leagues, and every pick after it went nowhere. Connecting a league IS
    intent to keep it; asking for a second, separate confirmation was a step
    whose only possible outcome was losing a draft.

    Names default to something recognisable and stay editable, so Save became
    a rename rather than a commit.
    """
    if STATE.league_id:
        return

    # REUSE THE EXISTING RECORD FOR THIS LEAGUE. Minting an id on every connect
    # means reconnecting after a reload -- or a restart, which happens -- files
    # the same league again under a new row. Ten connects, ten identical
    # entries in My Leagues, and the older ones quietly stop receiving picks.
    if STATE.espn:
        key = str(STATE.espn.get("league_id"))
        season = STATE.espn.get("season")
        for row in store.listing():
            if (str(row.get("espn_league_id")) == key
                    and row.get("season", season) == season):
                STATE.league_id = row["id"]
                STATE.name = STATE.name or row.get("name") or name
                _autosave(force=True)
                return

    STATE.league_id = store.new_id()
    STATE.name = STATE.name or name
    _autosave(force=True)


def _snapshot() -> dict:
    """Everything needed to resume this draft exactly where it is."""
    st = STATE
    return {
        "name": st.name,
        "platform": st.platform,
        "n_teams": st.settings.n_teams,
        "roster_size": st.settings.roster_size,
        "lineup": dict(st.settings.lineup),
        "points_per_reception": st.settings.points_per_reception,
        "my_slot": st.my_slot,
        "owned_picks": st.owned_picks,
        "risk": st.risk,
        "bench_risk": st.bench_risk,
        "picks": st.picks,
        "espn": st.espn,
        "league_name": st.league_name,
        "team_names": {str(k): v for k, v in st.team_names.items()},
        "my_team_id": st.my_team_id,
        "slot_confirmed": st.slot_confirmed,
        "draft_time": st.draft_time,
        "pinned": dict(st.pinned),
        "pinned_week": st.pinned_week,
        "added": list(st.added),
        "dropped": list(st.dropped),
    }


# What was on disk the last time we wrote. The live sync fires every few
# seconds and almost always changes nothing, so writing on every call would be
# hundreds of pointless disk writes an hour during a draft.
_SAVED: tuple | None = None


def _fingerprint() -> tuple:
    """Everything worth persisting, reduced to something comparable."""
    st = STATE
    return (
        len(st.picks),
        st.my_slot,
        tuple(st.owned_picks) if st.owned_picks else None,
        st.my_team_id,
        st.slot_confirmed,
        st.draft_complete,
        st.risk,
        st.bench_risk,
        st.name,
        bool(st.espn),
        # Hand edits to the roster count as a change even when the pick log
        # has not moved -- after the draft they are the ONLY thing that moves.
        tuple(st.added),
        tuple(st.dropped),
        tuple(sorted(st.pinned.items())),
    )


def _autosave(force: bool = False) -> None:
    """Persist after every mutation, so closing the tab costs nothing.

    Named once, saved forever after: `league_id` is set by /leagues/save and
    every later change writes itself. There is no second button to forget.

    Skips the write when nothing has actually changed, which matters because
    the live-draft poll calls through here every few seconds and is usually
    reporting that the board is exactly as it was.
    """
    global _SAVED
    if not STATE.league_id or STATE.settings is None:
        return
    fp = _fingerprint()
    if not force and fp == _SAVED:
        return
    try:
        store.save(STATE.league_id, _snapshot())
        _SAVED = fp
    except Exception:      # a failed save must never break a draft
        pass


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LeagueIn(BaseModel):
    platform: str = "espn"        # whose ADP prices the board
    n_teams: int = 12
    my_slot: int = 1
    points_per_reception: float = 1.0
    roster_size: int = 16
    lineup: dict[str, int] = Field(
        default_factory=lambda: {"QB": 1, "RB": 2, "WR": 2, "TE": 1,
                                 "FLEX": 1, "K": 1, "DST": 1})
    risk_tolerance: str = "combined"
    bench_tolerance: str = "aggressive"


class EspnIn(BaseModel):
    league_id: str
    season: int = 2026
    espn_s2: str | None = None
    swid: str | None = None
    adopt_settings: bool = True


class PickIn(BaseModel):
    name: str | None = None
    player_id: str | None = None
    mine: bool = False


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

@app.post("/api/league")
def set_league(cfg: LeagueIn) -> dict:
    """Configure by hand. Any league, any platform -- nothing ESPN-specific."""
    lineup = {k: v for k, v in cfg.lineup.items() if v > 0}
    if not 1 <= cfg.my_slot <= cfg.n_teams:
        raise HTTPException(400, f"slot must be 1..{cfg.n_teams}")
    with STATE.lock:
        try:
            D.set_platform(cfg.platform)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        STATE.platform = cfg.platform
        STATE.settings = LeagueSettings(
            n_teams=cfg.n_teams, lineup=lineup,
            points_per_reception=cfg.points_per_reception,
            roster_size=cfg.roster_size)
        STATE.my_slot = cfg.my_slot
        STATE.risk = cfg.risk_tolerance
        STATE.bench_risk = cfg.bench_tolerance
        STATE.picks = []
        STATE.owned_picks = None
        # A new league is a new file. Without this, configuring one would
        # autosave straight over whichever league was open before.
        STATE.league_id = None
        STATE.name = ""
        STATE.slot_confirmed = True     # you told us the seat yourself
        STATE.espn = None
        STATE.my_team_id = None
        STATE.draft_started = False
        STATE.draft_complete = False
        D._BOARD = None
    _adopt(STATE.settings.describe().split("|")[0].strip())
    return status()


@app.post("/api/espn/connect")
def connect_espn(cfg: EspnIn) -> dict:
    """Attach a real ESPN league so picks arrive on their own.

    Reads the league's own settings by default, which is how this works for
    anyone's league rather than only a 12-team full-PPR one.
    """
    s2 = cfg.espn_s2 or os.getenv("ESPN_S2")
    swid = cfg.swid or os.getenv("SWID")
    try:
        payload = espn_draft.fetch(cfg.league_id, cfg.season, s2, swid)
    except espn_draft.DraftUnavailable as exc:
        raise HTTPException(400, str(exc)) from exc

    info = espn_draft.league_info(payload)
    with STATE.lock:
        STATE.espn = {"league_id": cfg.league_id, "season": cfg.season,
                      "espn_s2": s2, "swid": swid}
        STATE.platform = "espn"
        D.set_platform("espn")
        STATE.league_name = info.name
        STATE.team_names = {t["id"]: t["name"] for t in info.teams}
        if cfg.adopt_settings:
            STATE.settings = LeagueSettings(
                n_teams=info.n_teams, lineup=info.lineup,
                points_per_reception=info.points_per_reception,
                roster_size=info.roster_size)
            D._BOARD = None
        tid = espn_draft.my_team_id(payload, swid)
        STATE.swid_matched = tid is not None
        if tid:
            STATE.my_team_id = tid
            slot = espn_draft.draft_slot(payload, tid)
            if slot:
                STATE.my_slot = slot
                STATE.slot_confirmed = True
    _ingest(payload)
    _adopt(STATE.league_name or f"ESPN {cfg.league_id}")
    _autosave()
    return status()


@app.post("/api/slot")
def set_slot(slot: int) -> dict:
    """Set your draft seat by hand, for when ESPN has not published it yet."""
    st = _require()
    if not 1 <= slot <= st.settings.n_teams:
        raise HTTPException(400, f"slot must be 1..{st.settings.n_teams}")
    with STATE.lock:
        STATE.my_slot = slot
        STATE.slot_confirmed = True
        STATE.owned_picks = None    # back to the plain snake off the new seat
    _autosave()
    return status()


class OwnedPicks(BaseModel):
    picks: list[int]


@app.post("/api/picks/mine")
def set_owned_picks(body: OwnedPicks) -> dict:
    """Say exactly which overall picks are yours, after trades.

    The snake formula stops describing your picks the moment you trade one.
    Sending round five away doubles the wait from round four, and that gap is
    the input every survival probability is built on -- so it cannot be
    inferred, it has to be stated.
    """
    st = _require()
    total = st.settings.total_picks
    bad = [p for p in body.picks if not 1 <= p <= total]
    if bad:
        raise HTTPException(400, f"picks outside 1..{total}: {bad[:5]}")
    with STATE.lock:
        STATE.owned_picks = sorted(set(body.picks))
    _autosave()
    return status()


@app.post("/api/picks/reset")
def reset_owned_picks() -> dict:
    """Back to the untraded snake for your seat."""
    _require()
    with STATE.lock:
        STATE.owned_picks = None
    _autosave()
    return status()


@app.get("/api/search")
def search(q: str, limit: int = 8) -> dict:
    """Name lookup for the pick box, so nobody has to spell Smith-Njigba."""
    st = _require()
    term = q.strip().lower()
    if len(term) < 2:
        return {"players": []}
    taken = set(st.drafted_ids)
    b = _board()
    hits = (
        b.filter(pl.col("player_name").str.to_lowercase().str.contains(term,
                                                                       literal=True))
        .sort("ecr", nulls_last=True)
        .head(limit * 3)
    )
    shots = _headshots(b)
    out = []
    for r in hits.iter_rows(named=True):
        out.append({
            "player_id": r["player_id"],
            "player_name": r["player_name"],
            "position": r["position"],
            "ecr": r.get("ecr"),
            "vor": round(r["vor"], 1) if r.get("vor") is not None else None,
            "drafted": r["player_id"] in taken,
            "headshot": shots.get(r["player_id"]),
        })
    # Undrafted first: you are almost always naming someone still available.
    out.sort(key=lambda x: (x["drafted"], x["ecr"] or 9e9))
    return {"players": out[:limit]}


def _name_key(s: str) -> str:
    """Same normalisation the board uses, for matching across id systems."""
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\.?$", "", (s or "").lower().strip())
    return re.sub(r"[^a-z ]", "", s).strip()


def _resolve(board: pl.DataFrame, gsis: str | None, espn_id: str | None,
             name: str | None) -> str | None:
    """An ESPN player, as the board spells him.

    Three keys, in falling order of trust, because the board itself is keyed
    two different ways and the crosswalk lags a draft class:

      gsis            everyone the crosswalk can place
      espn-<id>       kickers and defences, which have no gsis and never will
      the name        rookies the crosswalk has not caught up with yet

    Returning None is a real answer -- a player genuinely off the board cannot
    be priced -- but it must be the LAST answer, not the first. It was the
    first, and every kicker and defence in the league quietly disappeared.
    """
    ids, names = _board_keys(board)
    if gsis and gsis in ids:
        return gsis
    if espn_id and f"espn-{espn_id}" in ids:
        return f"espn-{espn_id}"
    if name:
        return names.get(_name_key(name))
    return None


# Keyed on the board object, so a rebuilt board (a platform switch does that)
# never gets answered out of the previous one's index.
_KEYS: tuple[int, set[str], dict[str, str]] | None = None


def _board_keys(board: pl.DataFrame) -> tuple[set[str], dict[str, str]]:
    global _KEYS
    if _KEYS is None or _KEYS[0] != id(board):
        _KEYS = (
            id(board),
            set(board["player_id"].to_list()),
            {_name_key(n): i for n, i in zip(board["player_name"].to_list(),
                                             board["player_id"].to_list())},
        )
    return _KEYS[1], _KEYS[2]


def _live_rosters(payload: dict, board: pl.DataFrame) -> dict[int, list[str]]:
    """team_id -> board ids, from ESPN's current rosters.

    Every id goes through `_resolve`, so a kicker keyed `espn-<id>` on the
    board arrives spelled the way the board spells him rather than the way
    ESPN does. Players genuinely off the board are dropped here: they cannot
    be priced, and a roster row with no projection would be counted as a zero.
    """
    try:
        r = espn_draft.rosters(payload)
    except Exception:
        return {}
    if not r.height:
        return {}
    ids, _ = _board_keys(board)
    out: dict[int, list[str]] = {}
    for row in r.iter_rows(named=True):
        pid = row.get("player_id")
        if pid not in ids:
            pid = _resolve(board, row.get("gsis_id"), row.get("espn_id"),
                           row.get("player_name"))
        if pid:
            out.setdefault(int(row["team_id"]), []).append(pid)
    return out


def _settle(live: list[str]) -> None:
    """Retire hand edits ESPN has caught up with.

    An override that outlives the thing it was overriding stops being a
    correction and becomes a second, private copy of the roster -- so a man you
    added by hand and then genuinely lost in a trade would sit on your team for
    the rest of the season because you once typed his name. Kept only while
    they still disagree.
    """
    have = set(live)
    STATE.added = [p for p in STATE.added if p not in have]
    STATE.dropped = [p for p in STATE.dropped if p in have]


def _ingest(payload: dict) -> None:
    """Replace local pick history with ESPN's, which is authoritative."""
    info = espn_draft.league_info(payload)
    picks = espn_draft.picks(payload)
    total = STATE.settings.total_picks if STATE.settings else None

    with STATE.lock:
        STATE.last_sync = time.time()
        STATE.sync_error = None
        STATE.draft_started = bool(picks.height) or info.in_progress
        if info.draft_time:
            STATE.draft_time = info.draft_time
        STATE.draft_complete = bool(
            info.complete or (total and picks.height >= total))

    if not picks.height:
        # Connected early. Nothing to ingest, but do not leave a stale local
        # history behind either.
        with STATE.lock:
            STATE.picks = []
        return
    b = _board()
    teams = STATE.settings.n_teams

    fresh = []
    for r in picks.iter_rows(named=True):
        pid = _resolve(b, r.get("gsis_id"), r.get("espn_id"),
                       r.get("player_name"))
        slot = slot_for_pick(r["round"], r["round_pick"], teams) \
            if r["round"] and r["round_pick"] else None
        if slot is None:
            slot = ((r["overall"] - 1) % teams) + 1
        fresh.append({
            "player_id": pid,
            "espn_id": r.get("espn_id"),
            "name": r.get("player_name") or f"ESPN #{r.get('espn_id')}",
            "slot": slot,
            "team_id": r.get("team_id"),
            "overall": r["overall"],
        })
    live = _live_rosters(payload, b)
    with STATE.lock:
        STATE.picks = sorted(fresh, key=lambda p: p["overall"])
        if live:
            STATE.rosters = live
            _settle(live.get(STATE.my_team_id or -1, []))
        # Round one is the first chance to read the seat off real picks.
        if not STATE.slot_confirmed and STATE.my_team_id:
            slot = espn_draft.draft_slot(payload, STATE.my_team_id)
            if slot:
                STATE.my_slot = slot
                STATE.slot_confirmed = True


@app.post("/api/espn/sync")
def sync() -> dict:
    """Pull the latest picks. The front end calls this on a timer."""
    st = _require()
    if not st.espn:
        raise HTTPException(400, "no ESPN league connected")
    try:
        payload = espn_draft.fetch(**{k: v for k, v in st.espn.items()})
        _ingest(payload)
    except espn_draft.DraftUnavailable as exc:
        with STATE.lock:
            STATE.sync_error = str(exc)
    # Picks arriving from ESPN are the highest-frequency mutation there is, and
    # were the one kind that never reached disk. A tab closed mid-draft lost
    # every pick since the last one typed by hand.
    _autosave()
    return status()


# ---------------------------------------------------------------------------
# In-season sync
# ---------------------------------------------------------------------------
# Two clocks. Rosters are one cheap call and refresh on demand; the season is
# stats and depth charts, where new evidence only exists once games settle, so
# it runs weekly. Polling that hourly would re-read the same numbers and call
# it an update.

def _season_keeper() -> None:
    """Background refresh, for as long as the app is running.

    A laptop is asleep most of the week, so a calendar job alone will miss its
    slot and not notice. This checks every few hours while the app is open and
    `refresh.season` no-ops inside the weekly window, so the common case costs
    one comparison. Between the two, the data is current whenever you actually
    look at it -- which is the only time it matters.
    """
    while True:
        try:
            refresh.season()
        except Exception:
            pass          # a failed pull is never a reason to take the app down
        time.sleep(6 * 3600)


threading.Thread(target=_season_keeper, daemon=True, name="season-keeper").start()


@app.get("/api/season")
def season_status() -> dict:
    """What is fresh. Distinguishes stale from never-pulled, on purpose."""
    return refresh.describe()


@app.post("/api/season/sync")
def season_sync(force: bool = False) -> dict:
    """Pull this season's stats, depth charts and injuries.

    Safe to call whenever -- it no-ops inside the weekly window unless forced.
    In August most of these are simply not published yet, which is reported
    rather than raised.
    """
    refresh.season(force=force)
    return refresh.describe()


def _espn_rosters() -> pl.DataFrame:
    """Current ownership, with every id spelled the way the board spells it."""
    st = _require()
    if not st.espn:
        raise HTTPException(400, "no ESPN league connected")
    try:
        payload = espn_draft.fetch(**{k: v for k, v in st.espn.items()})
    except espn_draft.DraftUnavailable as exc:
        raise HTTPException(502, str(exc))

    r = espn_draft.rosters(payload)
    if not r.height:
        return r
    b = _board()
    ids, _ = _board_keys(b)
    fixed = [pid if pid in ids
             else (_resolve(b, row.get("gsis_id"), row.get("espn_id"),
                            row.get("player_name")) or pid)
             for pid, row in zip(r["player_id"].to_list(),
                                 r.iter_rows(named=True))]
    r = r.with_columns(pl.Series("player_id", fixed))
    with STATE.lock:
        STATE.rosters = {int(t): [p for p in grp["player_id"].to_list()
                                  if p in ids]
                         for t, grp in
                         ((tid[0] if isinstance(tid, tuple) else tid, g)
                          for tid, g in r.group_by("team_id"))}
        _settle(STATE.rosters.get(STATE.my_team_id or -1, []))
    return r


def _free_agents(board: pl.DataFrame) -> pl.DataFrame:
    """Everyone nobody in this league owns.

    THE PRICE OF AN EMPTY SLOT. Trading your only quarterback does not mean
    starting nobody there; it means starting whoever is left on the wire, and
    at quarterback that man is nearly as good as the one you sent. At running
    back the wire is bare and the same trade is close to ruinous. Which is
    which is a fact about this league right now, not a rule about positions --
    so it is read off the pool.

    Falls back to the draft log when live rosters are not available, which is
    the manual case: a player nobody drafted is a free agent there too.
    """
    owned: set[str] = set()
    for ids in (STATE.rosters or {}).values():
        owned.update(ids)
    if not owned:
        owned = set(STATE.drafted_ids)
    return board.filter(~pl.col("player_id").is_in(list(owned)))


def _team_frames(r: pl.DataFrame) -> tuple[pl.DataFrame, dict[int, pl.DataFrame]]:
    """Board rows for my roster and for each opponent's."""
    b = _board()
    mine = b.filter(pl.col("player_id").is_in(
        r.filter(pl.col("team_id") == STATE.my_team_id)["player_id"].to_list()))
    others: dict[int, pl.DataFrame] = {}
    for tid, grp in r.group_by("team_id"):
        team_id = int(tid[0] if isinstance(tid, tuple) else tid)
        if team_id == STATE.my_team_id:
            continue
        sub = b.filter(pl.col("player_id").is_in(grp["player_id"].to_list()))
        if sub.height:
            others[team_id] = sub
    return mine, others


@app.get("/api/rosters")
def league_rosters() -> dict:
    """Who owns whom RIGHT NOW, not who was drafted.

    The draft log freezes the moment the draft ends; waivers, drops and trades
    all happen after it. Trade mode needs current ownership, and the ESPN
    payload has carried it in `mRoster` all along.
    """
    r = _espn_rosters()
    if not r.height:
        return {"teams": [], "note": "ESPN returned no rosters for this league"}

    b = _board()
    val = b.select(["player_id", "projected_points", "vor"])
    r = r.join(val, on="player_id", how="left")
    shots = _headshots(b)

    out = []
    for tid, grp in r.group_by("team_id"):
        team_id = tid[0] if isinstance(tid, tuple) else tid
        players = (grp.sort("projected_points", descending=True, nulls_last=True)
                      .select(["player_id", "player_name", "position",
                               "lineup_slot", "starting", "injury_status",
                               "projected_points", "vor"]).to_dicts())
        for p in players:
            p["headshot"] = shots.get(p["player_id"])
        out.append({
            "team_id": team_id,
            "name": STATE.team_names.get(team_id, f"Team {team_id}"),
            "mine": team_id == STATE.my_team_id,
            "players": players,
        })
    return {"teams": sorted(out, key=lambda t: t["team_id"]),
            "as_of": time.time()}


class SwapIn(BaseModel):
    a: str
    b: str


@app.post("/api/roster/swap")
def roster_swap(x: SwapIn) -> dict:
    """Trade two of your own players' lineup slots.

    The lineup is solved optimally, which is right nearly always and wrong the
    moment you know something the projection does not -- a coach's comment, a
    matchup, a body you simply do not trust. Swapping pins BOTH men where you
    put them and leaves everyone else to solve around them, so one correction
    does not cost you the rest of the optimisation.
    """
    st = _require()
    b = _board()
    mine = set(st.my_ids)
    if x.a not in mine or x.b not in mine:
        raise HTTPException(400, "both players have to be on your roster")

    starters, bench = optimal_lineup(
        b.filter(pl.col("player_id").is_in(list(mine))), st.settings)
    slot_of = {r["player_id"]: r.get("slot")
               for r in starters.iter_rows(named=True)}
    slot_of.update({r["player_id"]: None for r in bench.iter_rows(named=True)})
    slot_of.update(STATE.pinned)

    sa, sb = slot_of.get(x.a), slot_of.get(x.b)
    if sa == sb:
        raise HTTPException(400, "those two are already in the same place")

    # A SLOT HAS RULES AND THE SWAP HAS TO OBEY THEM. Dragging a receiver onto
    # the defence put him there: the lineup then had a man in a slot he cannot
    # legally fill, and every number computed off it was quietly wrong. Only
    # the flex takes more than one position, and only the ones this league says.
    pos = dict(zip(b["player_id"].to_list(), b["position"].to_list()))
    for pid, slot in ((x.a, sb), (x.b, sa)):
        if not slot:
            continue
        if not fits(pos.get(pid), slot, st.settings.flex_eligible):
            raise HTTPException(
                400,
                f"{_named([pid])} is a {pos.get(pid)} and cannot play {slot}. "
                f"Only the flex takes more than one position, and in this "
                f"league it takes "
                f"{', '.join(st.settings.flex_eligible)}.")

    with STATE.lock:
        for pid, slot in ((x.a, sb), (x.b, sa)):
            if slot:
                STATE.pinned[pid] = slot
            else:
                STATE.pinned.pop(pid, None)
    _autosave()
    return {"pinned": dict(STATE.pinned)}


@app.post("/api/roster/unpin")
def roster_unpin() -> dict:
    """Back to the solved lineup."""
    _require()
    with STATE.lock:
        STATE.pinned = {}
        STATE.pinned_week = None
    _autosave()
    return {"pinned": {}}


@app.get("/api/team/report")
def team_report() -> dict:
    """Your team: what it does well, what it does not, and how you got it.

    Everything is measured against THIS league's startable pool rather than an
    absolute. A 240-point tight end is excellent where the median starter is
    150 and unremarkable where he is 230, and only the comparative version can
    be traded on.
    """
    st = _require()
    b = _board()
    mine = b.filter(pl.col("player_id").is_in(st.my_ids))
    if not mine.height:
        return {"empty": True,
                "note": "Nothing drafted yet — this fills in as you pick."}

    grade = grade_roster(mine, st.settings, b)
    starters, bench = optimal_lineup(mine, st.settings, pinned=STATE.pinned)
    strength = report.positional_strength(mine, b, st.settings)
    dr = report.draft_report(st.picks, b, st.my_ids)
    # KICKER AND DEFENCE ARE ON THE ROSTER AND OUT OF THE VERDICT. They have to
    # be on the team -- they fill two starting slots every week -- but they are
    # flat by construction, so "you are 12th at kicker" is a fact about the
    # draft order and not about your team. Left in, they were also the two
    # biggest bars on the chart, which said the opposite.
    shape = report.tradeable(strength)
    words = report.summarise(shape, grade)
    shots = _headshots(b)

    def rows(df: pl.DataFrame, starting: bool) -> list[dict]:
        if not df.height:
            return []
        keep = [c for c in ("player_id", "player_name", "position", "slot",
                            "projected_points", "vor", "ecr", "season_p20",
                            "season_p50", "season_p80", "expected_games")
                if c in df.columns]
        out = df.select(keep).to_dicts()
        for r in out:
            r["headshot"] = shots.get(r["player_id"])
            r["starting"] = starting
        return out

    return {
        "empty": False,
        "grade": {k: v for k, v in grade.items()
                  if k not in ("lineup", "bench")},
        "starters": rows(starters, True),
        "bench": rows(bench, False),
        "strength": shape,
        # Which starting slots are still empty. The shape panel no longer
        # mentions kicker and defence, so this is where a missing one shows up
        # -- and it shows up as an empty slot on the lineup card, which is
        # where you would look for it anyway.
        "gaps": report.unfilled(starters, st.settings),
        "byes": report.bye_conflicts(
            report.with_byes(mine, config.PRODUCTION_TARGET_SEASON),
            st.settings),
        "draft": dr,
        "pinned": dict(STATE.pinned),
        "pinned_week": STATE.pinned_week,
        "league": report.league_comparison(b, st.picks, st.settings,
                                          st.my_ids, st.my_slot,
                                          rosters=STATE.rosters,
                                          my_team_id=STATE.my_team_id),
        "sleepers": report.sleepers(b, st.my_ids),
        "improve": report.improvements(strength, b, st.drafted_ids,
                                       st.settings, roster=mine),
        "team_names": {str(k): v for k, v in STATE.team_names.items()},
        "complete": bool(st.draft_complete
                         or st.on_the_clock()[2] > st.settings.total_picks
                         or len(st.my_ids) >= st.settings.roster_size),
        "strengths": words["strengths"],
        "weaknesses": words["weaknesses"],
    }


# ESPN's own injury tag, which is the ONLY injury report that exists in
# August: nflverse publishes none until the season starts (its loader caps at
# last season) and no preseason box scores at all, ever. ESPN's is live, it is
# already in the roster payload, and it is what a manager is looking at.
OUT_FOR_THE_WEEK = frozenset({"OUT", "INJURY_RESERVE", "SUSPENSION", "DOUBTFUL",
                              "NOT_ACTIVE"})


class ApplyIn(BaseModel):
    pinned: dict[str, str] = Field(default_factory=dict)
    # Which week this lineup was solved for. The roster panel says "set by
    # hand" about pinned men, which is a lie about a lineup the app itself
    # worked out -- and the difference matters, because one of them you want
    # to keep and the other you want to clear when the week turns over.
    week: int | None = None


@app.post("/api/roster/apply")
def roster_apply(x: ApplyIn) -> dict:
    """Set the whole lineup at once, from a suggestion."""
    _require()
    with STATE.lock:
        STATE.pinned = dict(x.pinned)
        STATE.pinned_week = x.week
    _autosave()
    return {"pinned": dict(STATE.pinned), "pinned_week": STATE.pinned_week}


@app.get("/api/team/suggest")
def team_suggest(week: int | None = None) -> dict:
    """The lineup to start in a given week, and what changes from the one set.

    A LINEUP IS A WEEKLY QUESTION and the solver behind My Team answers a
    seasonal one. Most weeks the two agree; the weeks they do not are exactly
    the weeks people lose. A man on bye scores zero, a man ESPN lists OUT
    scores zero, and both are invisible to a season projection -- which is why
    a roster that looks right in August starts a bye week in October.

    What is week-specific TODAY is byes and injury status, and it says so
    rather than implying a matchup model that does not exist yet. When weekly
    projections arrive the same endpoint gets better inputs and the interface
    does not change.
    """
    st = _require()
    b = _board()

    # THE WEEK IS DERIVED, NOT CHOSEN. There is exactly one week whose lineup
    # you can still set -- the next one nobody has played -- so asking which
    # one was a control with a single right answer. Before kickoff that is week
    # one; after it, the week following the last set of results.
    stamp0 = refresh.read_stamp()
    if week is None:
        week = min((stamp0.week or 0) + 1, 18) if stamp0.got_stats else 1

    # FORM, ONCE THERE IS ANY -- and consistent form, not one loud Sunday.
    # `inseason.reprice` blends the pre-season number with what the season has
    # shown, weighted by EFFECTIVE games: three steady weeks are worth nearly
    # three, three wild ones worth about one and a half. So a man who has been
    # quietly good climbs into the lineup and a man who had one thirty-point
    # afternoon does not.
    stamp = stamp0
    form: pl.DataFrame | None = None
    if stamp.got_stats:
        obs = refresh.observed()
        if obs.height:
            from fantasyedge.models import inseason

            b = inseason.reprice(b, obs, stamp.week or 0)
            form = obs

    mine = b.filter(pl.col("player_id").is_in(st.my_ids))
    if not mine.height:
        return {"empty": True, "note": "Nothing on your roster yet."}

    hurt = _injury_status()
    # WHOSE JOB JUST CAME OPEN. The man behind an injured starter is not the
    # player his season projection describes -- measured on 2025, an RB2 whose
    # starter sits goes 6.93 -> 13.00, and a WR2 barely moves, because carries
    # transfer whole and vacated targets scatter. That machinery was built,
    # measured, and inert, because it asked nflverse for an injury report that
    # does not exist until the season starts. ESPN has one today.
    #
    # It runs across the WHOLE LEAGUE, not just your roster: the starter who is
    # out is usually not your player, which is the entire point.
    out_now = {pid for pid, s in hurt.items()
               if (s or "").upper() in OUT_FOR_THE_WEEK}
    lifted: dict[str, float] = {}
    try:
        from fantasyedge.data import depth

        bumped = depth.vacancy(b, config.PRODUCTION_TARGET_SEASON, week,
                               injured=out_now)
        if "vacancy_mult" in bumped.columns:
            lifted = {r["player_id"]: float(r["vacancy_mult"])
                      for r in bumped.select(["player_id", "vacancy_mult"])
                                     .iter_rows(named=True)
                      if float(r["vacancy_mult"] or 1.0) > 1.0}
            if lifted:
                mine = bumped.filter(pl.col("player_id").is_in(st.my_ids))
    except Exception:
        pass

    mine = report.with_byes(mine, config.PRODUCTION_TARGET_SEASON)
    byes = {r["player_id"]: r.get("bye_week")
            for r in mine.iter_rows(named=True)} if "bye_week" in mine.columns else {}

    out_ids = [pid for pid in mine["player_id"].to_list()
               if (hurt.get(pid) or "").upper() in OUT_FOR_THE_WEEK
               or (byes.get(pid) is not None and int(byes[pid] or 0) == week)]

    # Solve on who can actually play. Everyone else is benched by the facts
    # rather than by an opinion about him.
    playable = mine.filter(~pl.col("player_id").is_in(out_ids))
    starters, bench = optimal_lineup(playable, st.settings)

    want = {r["player_id"]: (r.get("slot") or r["position"])
            for r in starters.iter_rows(named=True)}
    now, _ = optimal_lineup(mine, st.settings, pinned=STATE.pinned)
    have = {r["player_id"]: (r.get("slot") or r["position"])
            for r in now.iter_rows(named=True)}
    names = dict(zip(mine["player_id"].to_list(), mine["player_name"].to_list()))
    pts = dict(zip(mine["player_id"].to_list(),
                   mine["projected_points"].fill_null(0.0).to_list()))

    # PAIR BY POSITION, NOT BY SLOT. Benching a man on bye cascades -- his
    # RB1 slot is refilled from RB2, RB2 from the flex, and the man who
    # actually comes off the bench arrives at the bottom. Matching the two
    # lists slot-for-slot produced "KC Concepcion in for nobody", which is
    # true of the slot and useless about the trade-off.
    pos = dict(zip(mine["player_id"].to_list(), mine["position"].to_list()))
    gone = [q for q in have if q not in want]
    come = [p for p in want if p not in have]

    seen = {}
    if form is not None:
        seen = {r["player_id"]: r for r in form.iter_rows(named=True)}

    def reason(q: str) -> str:
        if byes.get(q) is not None and int(byes[q] or 0) == week:
            return "on bye this week"
        if (hurt.get(q) or "").upper() in OUT_FOR_THE_WEEK:
            return f"listed {(hurt.get(q) or 'out').lower().replace('_', ' ')}"
        f = seen.get(q)
        if f and (f.get("games") or 0) > 0:
            # Name the record, not just the conclusion: how many games, at
            # what rate, and whether that rate held up week to week.
            cv = f.get("cv")
            shape = ("steady" if cv is not None and cv < 0.5
                     else "erratic" if cv is not None else "so far")
            return (f"{int(f['games'])} games at {float(f['ppg']):.1f} "
                    f"a game, {shape}")
        return "beaten on projection"

    changes, used = [], set()
    for q in sorted(gone, key=lambda x: -pts.get(x, 0.0)):
        mate = next((p for p in come
                     if p not in used and pos.get(p) == pos.get(q)), None)
        if mate is None:
            mate = next((p for p in come if p not in used), None)
        if mate:
            used.add(mate)
        changes.append({
            "slot": want.get(mate, have.get(q)),
            "player_id": mate, "player_name": names.get(mate) if mate else None,
            "points": round(pts.get(mate, 0.0), 1) if mate else 0.0,
            "out_id": q, "out_name": names.get(q),
            "out_points": round(pts.get(q, 0.0), 1),
            "why": reason(q),
        })
    for p in come:
        if p in used:
            continue
        changes.append({
            "slot": want.get(p), "player_id": p, "player_name": names.get(p),
            "points": round(pts.get(p, 0.0), 1),
            "out_id": None, "out_name": None, "out_points": 0.0,
            "why": "a slot nobody was filling",
        })

    return {
        "empty": False,
        "week": week,
        "pinned": want,
        "changes": sorted(changes, key=lambda c: c["slot"]),
        "unavailable": [{"player_id": p, "player_name": names.get(p),
                         "reason": ("bye" if byes.get(p) == week
                                    else (hurt.get(p) or "out").lower())}
                        for p in out_ids],
        "byes": {str(w): [names.get(p) for p, bw in byes.items()
                          if bw is not None and int(bw or 0) == w]
                 for w in sorted({int(v) for v in byes.values() if v})},
        # Who this week's promotions are, so a lineup change that comes from
        # somebody else's injury says so rather than looking like a whim.
        "promoted": [{"player_id": pid, "player_name": names.get(pid),
                      "lift": round(mult, 2)}
                     for pid, mult in sorted(lifted.items(), key=lambda kv: -kv[1])
                     if pid in names],
        "note": (
            "Byes, injury status, jobs opened by somebody else's injury, and "
            "form so far — weighted by how CONSISTENT it has been, so three "
            "steady weeks count for nearly three and three wild ones for about "
            "one and a half. Opponent matchup is the one week-specific thing "
            "still missing; it needs a fit on real weeks."
            if form is not None else
            "Byes, injury status and jobs opened by somebody else's injury are "
            "the week-specific facts today. Form and opponent matchup arrive "
            "with the first real week — there is nothing to read yet."),
    }


def _injury_status() -> dict[str, str]:
    """player_id -> ESPN's live injury tag, for everyone in the league."""
    try:
        r = _espn_rosters()
    except Exception:
        return {}
    if not r.height or "injury_status" not in r.columns:
        return {}
    return {row["player_id"]: row["injury_status"]
            for row in r.iter_rows(named=True) if row.get("injury_status")}


def _team_rows(scope: str, team_id: int | None) -> pl.DataFrame | None:
    """Which players this view is about: mine, one team's, or everybody's.

    A LEAGUE VIEW OF TWELVE TEAMS IS TWELVE TEAMS, not the top twelve players
    in it. The first version listed the best scorers league-wide, which reads
    as a leaderboard and answers a question nobody asked -- you look at this to
    see whose men are beating their price, and "whose" needs a team.
    """
    b = _board()
    if scope == "mine":
        return b.filter(pl.col("player_id").is_in(STATE.my_ids))
    if team_id is not None:
        ids = (STATE.rosters or {}).get(int(team_id), [])
        return b.filter(pl.col("player_id").is_in(ids)) if ids else b.head(0)
    return None


def _league_teams(obs: pl.DataFrame | None) -> list[dict]:
    """Every team, with what its men were expected to do and what they did.

    Summed over the STARTING lineup rather than the whole roster: a bench full
    of disappointments is not what lost you a week, and a team is judged on
    what it puts on the field.
    """
    if not STATE.rosters:
        return []
    b = _board()
    st = STATE
    out = []
    for tid, ids in STATE.rosters.items():
        roster = b.filter(pl.col("player_id").is_in(ids))
        if not roster.height or st.settings is None:
            continue
        starters, _ = optimal_lineup(roster, st.settings)
        if not starters.height:
            continue
        exp = float((starters["projected_points"]
                     / starters["expected_games"].clip(1.0, None)).sum())
        row = {
            "team_id": int(tid),
            "name": STATE.team_names.get(int(tid), f"Team {tid}"),
            "mine": int(tid) == STATE.my_team_id,
            "players": roster.height,
            "expected_ppg": round(exp, 1),
            "ppg": None,
            "delta": 0.0,
        }
        if obs is not None and obs.height:
            j = starters.join(obs, on="player_id", how="inner")
            if j.height:
                got = float(j["ppg"].fill_null(0.0).sum())
                row["ppg"] = round(got, 1)
                row["delta"] = round(got - float(
                    (j["projected_points"] / j["expected_games"].clip(1.0, None)
                     ).sum()), 1)
        out.append(row)
    return sorted(out, key=lambda r: -(r["ppg"] if r["ppg"] is not None
                                       else r["expected_ppg"]))


def _expected_rows(scope: str, top: int, team_id: int | None = None) -> list[dict]:
    """What the projection expects per game, before anybody has played.

    The pre-season half of the performance panel. Same shape as the real rows
    so the interface does not need a second layout, with `ppg` and `delta`
    absent rather than zeroed -- a zero would be a measurement and there has
    not been one.
    """
    st = STATE
    b = _board()
    pool = _team_rows(scope, team_id)
    if pool is None:
        pool = b
    if not pool.height:
        return []
    shots = _headshots(b)
    mine = set(st.my_ids)
    rows = (pool.with_columns(
                (pl.col("projected_points")
                 / pl.col("expected_games").clip(1.0, None)).alias("exp_ppg"))
                .sort("exp_ppg", descending=True, nulls_last=True)
                .head(top))
    return [{
        "player_id": r["player_id"],
        "player_name": r["player_name"],
        "position": r["position"],
        "expected_ppg": round(float(r["exp_ppg"] or 0.0), 1),
        "ppg": None,
        "delta": 0.0,
        "games": None,
        "headshot": shots.get(r["player_id"]),
        "mine": r["player_id"] in mine,
    } for r in rows.iter_rows(named=True)]


# The last wire we priced, and what it was priced against. Waivers are the one
# screen meant to be left open and re-pulled -- an injury on Thursday changes
# the answer -- and pricing six positions four deep is four seconds of
# simulation. So the poll is cheap when nothing moved and honest when it did:
# the key is every input the answer depends on.
_WIRE: tuple[tuple, dict] | None = None


@app.get("/api/waivers")
def waivers(top: int = 12, force: bool = False) -> dict:
    """The wire, ranked by what each man would do to YOUR lineup.

    Not a list of the best free agents -- that list is identical for all twelve
    managers, which is the tell that it is not about anybody's roster. A claim
    is an add AND a drop, and both are priced here.
    """
    global _WIRE
    st = _require()
    b = _board()
    free = _free_agents(b)
    mine = b.filter(pl.col("player_id").is_in(st.my_ids))
    if not mine.height:
        return {"empty": True, "claims": [],
                "note": "Nothing on your roster yet."}

    # Form and role, the same two corrections the lineup solver uses, so the
    # wire is judged on what a man is now rather than what he cost in July.
    stamp = refresh.read_stamp()
    if stamp.got_stats:
        obs = refresh.observed()
        if obs.height:
            from fantasyedge.models import inseason

            b2 = inseason.reprice(b, obs, stamp.week or 0)
            free = b2.filter(pl.col("player_id").is_in(free["player_id"].to_list()))
            mine = b2.filter(pl.col("player_id").is_in(st.my_ids))

    hurt = _injury_status()
    key = (tuple(sorted(mine["player_id"].to_list())),
           len(free), hash(tuple(sorted(free["player_id"].to_list()))),
           stamp.week or 0, top,
           tuple(sorted((p, s) for p, s in hurt.items())))
    if not force and _WIRE is not None and _WIRE[0] == key:
        return _WIRE[1]

    # ONE search, two views of it. Every position is priced four deep and the
    # headline list is the best of those -- see waiver.best for why ranking the
    # wire twice put contradicting numbers on the same screen.
    groups = waiver.by_position(mine, free, st.settings, board=b,
                                deep=4, n_sims=2000)
    for men in groups.values():
        for c in men:
            c["why"] = waiver.why(c, mine, st.settings, free)
    rows = waiver.best(groups, top=top)

    full = mine.height >= st.settings.roster_size
    out = {
        "empty": False,
        "roster": mine.height,
        "limit": st.settings.roster_size,
        "full": full,
        "pool": free.height,
        "claims": _with_free_faces(rows, b),
        # EVERY position carries its next few, because waivers are a queue and
        # the man you want can be claimed before your priority comes up. A
        # single ranked list leaves you with nothing to do when he is gone.
        "positions": {pos: _with_free_faces(men, b)
                      for pos, men in groups.items()},
        "hurt": [{"player_id": p, "player_name": n, "position": pos,
                  "status": hurt[p]}
                 for p, n, pos in zip(mine["player_id"], mine["player_name"],
                                      mine["position"])
                 if hurt.get(p) and hurt[p] not in ("ACTIVE", "NORMAL")],
        "week": stamp.week or 0,
        "note": waiver.note(mine, st.settings, full, len(rows)),
        "streaming": ("Kicker and defence are ranked on the season here. Once "
                      "games start they are ranked on the WEEK, which is where "
                      "streaming them is actually worth something — the "
                      "softest matchup returns about 3.8 points a week more "
                      "than the toughest at defence."),
        "pulled": datetime.now().strftime("%H:%M"),
    }
    _WIRE = (key, out)
    return out


def _with_free_faces(rows: list[dict], board: pl.DataFrame) -> list[dict]:
    shots = _headshots(board)
    for r in rows:
        r["headshot"] = shots.get(r["player_id"])
    return rows


@app.get("/api/performance")
def performance(scope: str = "mine", top: int = 12,
                team_id: int | None = None) -> dict:
    """Projected against actual, once games have been played.

    Two scopes and they answer different questions. `mine` is whether MY men
    are doing what I drafted them to do -- the only version that changes a
    lineup. `league` is who is beating their price anywhere, which is where a
    waiver claim or a buy-low comes from.

    Before week one this returns nothing and says why. A performance panel that
    invents numbers out of an unplayed season is worse than an empty one.
    """
    st = _require()
    stamp = refresh.read_stamp()
    if not stamp.got_stats:
        # THE HALF OF THIS PANEL THAT EXISTS IN AUGUST is the expectation. It
        # is the same list, the same order and the same rows that gain an
        # actual in week one -- so the panel shows what it is going to measure
        # against instead of a sentence in the middle of an empty box. Nothing
        # here is invented: every number is the projection the board already
        # carries, divided by the games it already expects.
        return {"ready": False, "week": 0,
                "kickoff": refresh.kickoff(config.PRODUCTION_TARGET_SEASON),
                "note": "No games have been played yet. This fills in from "
                        "week one.",
                "expected": True,
                "teams": (_league_teams(None)
                          if scope == "league" and team_id is None else []),
                "team_id": team_id,
                "rows": ([] if scope == "league" and team_id is None
                         else _expected_rows(scope, top, team_id))}

    obs = refresh.observed()
    if not obs.height:
        return {"ready": False, "week": stamp.week or 0, "rows": [],
                "note": "No player results in the latest pull."}

    b = _board()
    j = b.join(obs, on="player_id", how="inner")
    if not j.height:
        return {"ready": False, "week": stamp.week or 0, "rows": [],
                "note": "Results are in but none of them match the board."}

    # Expected per game from the pre-season projection, against what he has
    # actually averaged. Per GAME, so a man who missed three weeks is judged on
    # the football he played rather than punished twice for the injury.
    j = j.with_columns([
        (pl.col("projected_points") / pl.col("expected_games").clip(1.0, None))
        .alias("expected_ppg"),
    ]).with_columns([
        (pl.col("ppg") - pl.col("expected_ppg")).alias("delta"),
    ])

    if scope == "mine":
        j = j.filter(pl.col("player_id").is_in(st.my_ids))
        j = j.sort("delta", descending=True)
    elif team_id is not None:
        ids = (STATE.rosters or {}).get(int(team_id), [])
        j = j.filter(pl.col("player_id").is_in(ids)).sort("delta", descending=True)
    else:
        # THE LEAGUE IS TWELVE TEAMS. Drilling into one of them lists its men;
        # the top level lists the teams, because "who is beating their price"
        # is a question about somebody's roster and a leaderboard hides whose.
        return {"ready": True, "week": stamp.week or 0, "scope": scope,
                "teams": _league_teams(obs), "team_id": None,
                "rows": [], "note": ""}

    shots = _headshots(b)
    keep = [c for c in ("player_id", "player_name", "position", "ecr",
                        "projected_points", "expected_ppg", "ppg", "games",
                        "delta", "cv") if c in j.columns]
    rows = j.select(keep).to_dicts()
    for r in rows:
        r["headshot"] = shots.get(r["player_id"])
        r["mine"] = r["player_id"] in set(st.my_ids)
    return {"ready": True, "week": stamp.week or 0, "scope": scope,
            "team_id": team_id, "teams": [], "rows": rows, "note": ""}


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------

def _named(ids: list[str]) -> str:
    """Player ids as names, for a message a person has to read."""
    try:
        r = _espn_rosters()
        by = {row["player_id"]: row["player_name"]
              for row in r.iter_rows(named=True)} if r.height else {}
    except Exception:
        by = {}
    return ", ".join(by.get(i, i) for i in ids)


def _with_faces(d: dict, board: pl.DataFrame) -> dict:
    """Attach headshots to the players named in a verdict or an offer.

    The board keys on gsis id and ESPN serves portraits by ESPN id, so the URL
    cannot be built client-side without shipping the crosswalk to the browser.
    """
    shots = _headshots(board)
    for key in ("give", "get", "dropped"):
        for p in d.get(key) or []:
            p["headshot"] = shots.get(p.get("player_id"))
    return d


class TradeIn(BaseModel):
    give: list[str] = Field(default_factory=list)
    get: list[str] = Field(default_factory=list)
    # Your roster, when the caller knows it better than we do. Manual mode has
    # no sync and no draft log, so it supplies one; automatic mode leaves this
    # empty and we read the live roster. Either way the verdict is a LINEUP
    # delta, which is meaningless without a lineup to change -- so the roster
    # is an input, not a lookup.
    roster: list[str] = Field(default_factory=list)


def _my_roster_frame(explicit: list[str] | None = None) -> pl.DataFrame:
    """Your team: what the caller gave us, else live ESPN, else the draft log."""
    b = _board()
    if explicit:
        return b.filter(pl.col("player_id").is_in(explicit))
    ids = STATE.my_ids
    if STATE.espn:
        try:
            payload = espn_draft.fetch(**{k: v for k, v in STATE.espn.items()})
            r = espn_draft.rosters(payload)
            if r.height and STATE.my_team_id is not None:
                live = r.filter(pl.col("team_id") == STATE.my_team_id)
                if live.height:
                    ids = live["player_id"].to_list()
        except Exception:
            pass          # fall back to the draft log rather than fail the call
    return b.filter(pl.col("player_id").is_in(ids))


@app.post("/api/trade/evaluate")
def trade_evaluate(t: TradeIn) -> dict:
    """Price an offer by what it does to the lineup you can field.

    Returns the naive value total alongside the simulated verdict on purpose:
    the gap between them IS the opportunity cost, and showing it is the only
    way to make "three for one is not fair" argue for itself.
    """
    st = _require()
    if not t.give and not t.get:
        raise HTTPException(400, "nothing to evaluate")

    mine = _my_roster_frame(t.roster)

    b = _board()
    known = set(b["player_id"].to_list())
    unknown = [p for p in (t.give + t.get) if p not in known]
    if unknown:
        # NAME THEM. A list of gsis ids is unreadable, and the men who land
        # here are real: ten players on live rosters in his league sit outside
        # the ADP board the projections are built from, so they have no
        # projection to trade on. Saying which one, and why, is the difference
        # between a bug report and an explanation.
        who = _named(unknown)
        raise HTTPException(
            400, f"{who} {'is' if len(unknown) == 1 else 'are'} not on the "
                 f"projection board — nobody drafted {'him' if len(unknown) == 1 else 'them'} "
                 f"inside the top 600, so there is no number to trade on. "
                 f"Take {'him' if len(unknown) == 1 else 'them'} out of the deal.")

    # No roster is a legitimate question, not an error. People speculate about
    # players they do not own yet -- a deal two moves away, or a name they are
    # chasing. Answer it, but as a package comparison, and say plainly that the
    # roster-spot cost is the one thing it cannot see.
    if not mine.height:
        v = trade.compare_packages(b, t.give, t.get)
        d = _with_faces(v.as_dict(), b)
        call, line = trade.explain.headline(d)
        d.update({"call": call, "summary": line, "pros": [], "cons": []})
        return d

    v = trade.evaluate(mine, t.give, t.get, st.settings, b,
                       free_agents=_free_agents(b))
    d = _with_faces(v.as_dict(), b)

    # The roster as it would be afterwards, so the reasons are computed from
    # the same lineup the verdict came from rather than described from memory.
    after_ids = ([p for p in mine["player_id"].to_list() if p not in set(t.give)]
                 + list(t.get))
    dropped = {p["player_id"] for p in d.get("dropped") or []}
    after = b.filter(pl.col("player_id").is_in([x for x in after_ids
                                               if x not in dropped]))
    d.update(trade.explain.reasons(mine, after, st.settings, d))
    # The lineup card, before and after. Four separate "your WR2 slot gets
    # better" reasons described one cascade as if it were four events, which
    # reads as a bug -- one receiver arrived, so how did three slots improve?
    d["moves"] = trade.explain.lineup_moves(mine, after, st.settings)
    # A slot the trade empties is filled off the wire, not left blank -- so the
    # card names the man you would actually be starting there.
    fills = {f["slot"]: f for f in d.get("streamed") or []}
    for m in d["moves"]:
        if m["in"] is None and m["slot"].rstrip("0123456789") in fills:
            f = fills[m["slot"].rstrip("0123456789")]
            m.update({"in": f["player_name"], "in_id": f["player_id"],
                      "in_points": f["points"], "waiver": True,
                      "delta": round(f["points"] - m["out_points"], 1)})
    # WHO THE MEN YOU ARE BUYING SHARE A JOB WITH. Facts, not an adjustment:
    # a committee is usually already in the price, and saying so is more use
    # than a warning that double-counts what the market settled in July.
    try:
        from fantasyedge.data import depth
        d["roles"] = depth.roles(b, list(t.get),
                                 config.PRODUCTION_TARGET_SEASON)
    except Exception:
        d["roles"] = []
    # WHAT IT IS WORTH IN GENERAL, beside what it is worth to you. The gap
    # between the two is the difference between a steal and a fit, and it is
    # only answerable because every roster in the league is already read.
    if STATE.rosters:
        others = {tid: b.filter(pl.col("player_id").is_in(ids))
                  for tid, ids in STATE.rosters.items()
                  if tid != STATE.my_team_id}
        d["general"] = trade.for_everyone(others, t.give, t.get, st.settings, b,
                                          free_agents=_free_agents(b))
        d["fit"] = trade.explain.fit(d)
    call, line = trade.explain.headline(d)
    d.update({"call": call, "summary": line})
    return d


class ScanIn(BaseModel):
    """A scan, plus whatever you said was wrong with the last one."""
    stance: str = "fair"
    per_team: int = 1
    top: int = 8
    # Narrow it to one manager. The league read still comes back whole -- you
    # picked that manager for a reason and the reason is on the other rosters.
    team_id: int | None = None
    keep: list[str] = Field(default_factory=list)
    want: list[str] = Field(default_factory=list)
    must_get: list[str] = Field(default_factory=list)
    fewer: bool = False        # send fewer players
    richer: bool = False       # I need more back
    harder: bool = False       # they would never accept that
    seen: list[str] = Field(default_factory=list)
    note: str = ""             # free text, read by keyword and reported back


@app.post("/api/trade/scan")
def trade_scan(s: ScanIn) -> dict:
    """Read the whole league, then find the deals worth sending.

    Two answers in one call because they are one question. Offers alone say
    what to send and never say WHO TO TALK TO, and that is what people ask
    first -- the best deal in the league is worthless with a manager who has
    nothing you need. So every roster comes back read the same way (strong
    where, thin where, who is spare) beside the offers themselves.
    """
    st = _require()
    r = _espn_rosters()
    if not r.height:
        return {"offers": [], "teams": [],
                "note": "ESPN returned no rosters for this league"}

    b = _board()
    mine, others = _team_frames(r)
    if not mine.height:
        raise HTTPException(400, "could not identify your roster")

    read = trade.league.scan(mine, others, b, st.settings,
                             names=STATE.team_names,
                             my_team_id=STATE.my_team_id)

    # What you asked for, from the chips and from the sentence. Both land in
    # the same object and the object is returned, so the interface can show
    # what was understood rather than claim it understood.
    names_mine = dict(zip(mine["player_id"].to_list(),
                          mine["player_name"].to_list()))
    theirs_all = {}
    for sub in others.values():
        theirs_all.update(dict(zip(sub["player_id"].to_list(),
                                   sub["player_name"].to_list())))
    a = trade.ask.Ask(keep=list(s.keep), want=list(s.want),
                      must_get=list(s.must_get),
                      max_out=1 if s.fewer else 2,
                      richer=s.richer, harder=s.harder)
    a.read = ([f"keep {names_mine.get(p, p)}" for p in s.keep]
              + [f"you want a {p}" for p in s.want]
              + [f"target {theirs_all.get(p, p)}" for p in s.must_get]
              + (["send fewer players"] if s.fewer else [])
              + (["get more back"] if s.richer else [])
              + (["make it easier for them to say yes"] if s.harder else []))
    a = trade.ask.parse(s.note, names_mine, theirs_all, base=a)

    stamp = refresh.read_stamp()
    obs = refresh.observed() if stamp.got_stats else None
    week = stamp.week or 0

    pool = ({s.team_id: others[s.team_id]}
            if s.team_id is not None and s.team_id in others else others)

    stance = "conservative" if a.harder else s.stance
    prev = trade.suggest.THEIR_MIN_GAIN
    trade.suggest.THEIR_MIN_GAIN = trade.suggest.STANCE.get(stance, prev)
    try:
        offers = trade.suggest.across_league(
            mine, pool, b, st.settings, names=STATE.team_names,
            observed=obs, through_week=week, per_team=s.per_team, top=s.top,
            ask=a, seen=set(s.seen), free_agents=_free_agents(b))
    finally:
        trade.suggest.THEIR_MIN_GAIN = prev

    return {
        "offers": [_with_faces(o.as_dict(), b) for o in offers],
        "keys": [trade.suggest.key([p["player_id"] for p in o.give],
                                   [p["player_id"] for p in o.get])
                 for o in offers],
        "me": read["me"],
        "teams": read["teams"],
        "understood": a.read,
        "through_week": week,
        "note": "" if offers else _nothing_found(a),
    }


def _nothing_found(a) -> str:
    """Why the search came back empty, which is never just 'no results'."""
    if a.read:
        return ("Nothing fits that. Loosen one of the conditions above — "
                "each one cuts the search, and together they can cut all of it.")
    return ("Nothing worth offering right now — every roster is priced about "
            "right against yours, or the deals that help you would not read "
            "as wins for them.")


class CounterIn(BaseModel):
    give: list[str] = Field(default_factory=list)
    get: list[str] = Field(default_factory=list)
    team_id: int | None = None
    # The two rosters, when you typed them. Manual mode has no sync and no
    # counterparty to look up, and "find me a better version of this" is if
    # anything MORE useful there -- you are speculating, so every version is
    # equally hypothetical. Refusing to answer without ESPN made the button
    # vanish exactly where the search is cheapest to trust.
    roster: list[str] = Field(default_factory=list)
    their_roster: list[str] = Field(default_factory=list)
    stance: str = "fair"
    keep: list[str] = Field(default_factory=list)
    want: list[str] = Field(default_factory=list)
    fewer: bool = False
    richer: bool = False
    harder: bool = False
    seen: list[str] = Field(default_factory=list)
    note: str = ""


@app.post("/api/trade/balance")
def trade_balance(c: CounterIn) -> dict:
    """Keep this trade and even it out.

    Counter looks for a BETTER deal; this one keeps the deal you have and asks
    what closes the gap. Both men on the table stay, and the smallest sweetener
    that makes the two scales meet comes back with the arithmetic that says so.
    """
    st = _require()
    b = _board()
    if c.roster or c.their_roster:
        mine = b.filter(pl.col("player_id").is_in(c.roster))
        theirs = b.filter(pl.col("player_id").is_in(c.their_roster))
    else:
        r = _espn_rosters()
        if c.team_id is None:
            raise HTTPException(400, "which team are you trading with?")
        mine, others = _team_frames(r)
        theirs = others.get(int(c.team_id), b.head(0))
    if not mine.height or not theirs.height:
        raise HTTPException(400, "I need both rosters to balance a trade.")

    stamp = refresh.read_stamp()
    obs = refresh.observed() if stamp.got_stats else None

    # What it is worth now, so the answer can say what changed rather than
    # simply asserting the new one is better.
    now = trade.evaluate(mine, c.give, c.get, st.settings, b,
                         n_sims=trade.suggest.SCAN_SIMS,
                         free_agents=_free_agents(b))
    offers = trade.suggest.balance(mine, theirs, b, st.settings, c.give, c.get,
                                   observed=obs, through_week=stamp.week or 0,
                                   free_agents=_free_agents(b))

    core = set(c.give) | set(c.get)
    out = []
    for o in offers:
        added_get = [p for p in o.get if p["player_id"] not in core]
        added_give = [p for p in o.give if p["player_id"] not in core]
        d = _with_faces(o.as_dict(), b)
        d["adds"] = {"you_get": added_get, "you_give": added_give}
        theirs = trade.suggest.acceptance(o.their_gain, o.their_lineup)
        d["gap"] = round(o.our_gain - theirs, 1)
        d["even"] = bool(o.our_gain >= -trade.suggest.OUR_MIN_GAIN
                         and theirs >= -trade.suggest.OUR_MIN_GAIN)
        d["why"] = _why_balanced(now.delta_median, o, added_get, added_give)
        out.append(d)
    return {"offers": out,
            "before": {"our_gain": round(now.delta_median, 1)},
            "note": "" if out else
                    "Nothing on either bench closes this gap without changing "
                    "the deal. The pieces that would move the number are the "
                    "ones already on the table."}


def _why_balanced(was: float, o, added_get: list[dict],
                  added_give: list[dict]) -> str:
    """One sentence, every figure taken from the two verdicts."""
    theirs = trade.suggest.acceptance(o.their_gain, o.their_lineup)
    gap = round(abs(o.our_gain - theirs))
    parts = []
    if added_get:
        parts.append("they add "
                     + ", ".join(p["player_name"] for p in added_get))
    if added_give:
        parts.append("you add "
                     + ", ".join(p["player_name"] for p in added_give))
    who = " and ".join(parts) if parts else "nothing changes"

    move = (f"moves you from {round(was):+d} to {round(o.our_gain):+d}"
            if abs(o.our_gain - was) >= 1 else
            f"holds you at {round(o.our_gain):+d}")
    pts = "point" if gap == 1 else "points"
    even = (o.our_gain >= -trade.suggest.OUR_MIN_GAIN
            and theirs >= -trade.suggest.OUR_MIN_GAIN)
    if even and gap <= trade.suggest.OUR_MIN_GAIN * 2:
        tail = f"the two sides land {gap} {pts} apart"
    elif even:
        # Both can sign it and it is not a straight swap -- worth saying which,
        # because "even" and "21 points apart" together read as a contradiction.
        tail = (f"neither of you loses on it, though it is not level: you land "
                f"{round(o.our_gain):+d} and they land {round(theirs):+d}")
    else:
        tail = (f"it is the closest version there is and still leaves {gap} "
                f"{pts} between you")
    return (f"{who[0].upper()}{who[1:]}, which {move} on your lineup while "
            f"reading as {round(theirs):+d} from their side — {tail}.")


@app.post("/api/trade/counter")
def trade_counter(c: CounterIn) -> dict:
    """Better versions of the deal currently on the table.

    A search, not a rule. "Ask for one more small player" is the obvious move
    and usually the wrong one -- a small player is exactly what costs a roster
    spot and starts for nobody.
    """
    st = _require()
    b = _board()

    # EITHER list means you typed them, so answer on what you typed. Requiring
    # both non-empty sent a half-filled manual trade down the ESPN path, where
    # it came back asking which team you were trading with -- a question manual
    # mode does not have an answer to.
    if c.roster or c.their_roster:
        mine = b.filter(pl.col("player_id").is_in(c.roster))
        theirs = b.filter(pl.col("player_id").is_in(c.their_roster))
    else:
        r = _espn_rosters()
        tid = c.team_id
        if tid is None:
            raise HTTPException(400, "which team are you trading with?")
        mine, others = _team_frames(r)
        theirs = others.get(int(tid), b.head(0))
    if not mine.height or not theirs.height:
        raise HTTPException(
            400, "I need both rosters to look for a better version — in "
                 "manual mode, type the other side's players in too.")

    a = trade.ask.Ask(keep=list(c.keep), want=list(c.want),
                      max_out=1 if c.fewer else 2,
                      richer=c.richer, harder=c.harder)
    a = trade.ask.parse(
        c.note,
        dict(zip(mine["player_id"].to_list(), mine["player_name"].to_list())),
        dict(zip(theirs["player_id"].to_list(), theirs["player_name"].to_list())),
        base=a)

    stamp = refresh.read_stamp()
    obs = refresh.observed() if stamp.got_stats else None
    offers = trade.suggest.counter(
        mine, theirs, b, st.settings, c.give, c.get, stance=c.stance,
        observed=obs, through_week=stamp.week or 0, ask=a, seen=set(c.seen),
        free_agents=_free_agents(b))
    return {"offers": [_with_faces(o.as_dict(), b) for o in offers],
            "keys": [trade.suggest.key([p["player_id"] for p in o.give],
                                       [p["player_id"] for p in o.get])
                     for o in offers],
            "understood": a.read}


# ---------------------------------------------------------------------------
# Draft flow
# ---------------------------------------------------------------------------

@app.post("/api/pick")
def add_pick(p: PickIn) -> dict:
    """Record a pick by hand. Works with or without ESPN attached."""
    st = _require()
    b = _board()
    if p.player_id:
        hit = b.filter(pl.col("player_id") == p.player_id)
        hit = hit.to_dicts()[0] if hit.height else None
    elif p.name:
        hit = D.find(b, p.name, st.drafted_ids)
    else:
        raise HTTPException(400, "need a name or player_id")
    if not hit:
        raise HTTPException(404, f"no player matching {p.name or p.player_id!r}")

    rnd, pick, overall = st.on_the_clock()
    slot = slot_for_pick(rnd, pick, st.settings.n_teams)

    # A full roster is a real stop. Recording an 18th man on a 16-man team is
    # not a draft, it is a typo, and letting it through quietly corrupts every
    # lineup and grade downstream.
    if p.mine and len(st.my_ids) >= st.settings.roster_size:
        raise HTTPException(
            400,
            f"Your roster is full at {st.settings.roster_size}. Remove someone "
            f"first if this is a correction.")

    with STATE.lock:
        # WHICH PLAYERS ARE YOURS and WHICH PICK NUMBERS ARE YOURS are two
        # different facts, and treating them as one is what broke manual mode.
        # Taking eighteen men straight off the board rewrote the schedule to
        # "I own picks 1 through 18", which then poisoned the wait until your
        # next turn and with it every survival probability on the board.
        #
        # Ownership of a player is recorded here, on the pick. Ownership of a
        # pick number is a schedule, and it is edited deliberately in the pick
        # editor -- never as a side effect of drafting somebody.
        STATE.picks.append({
            "player_id": hit["player_id"], "name": hit["player_name"],
            "slot": slot, "overall": overall, "team_id": None,
            "mine": bool(p.mine),
        })
        # AND ON THE ROSTER, once the draft is over. After it, ownership is
        # read from ESPN rather than from this log, so recording the pick and
        # stopping there put the man nowhere you could see him -- the request
        # succeeded, the roster did not move, and the control read as broken.
        if p.mine and STATE.draft_complete:
            pid = hit["player_id"]
            if pid not in STATE.added:
                STATE.added.append(pid)
            if pid in STATE.dropped:
                STATE.dropped.remove(pid)
    _autosave()
    return status()


@app.post("/api/undo")
def undo() -> dict:
    """Take back the most recent pick, whoever made it."""
    _require()
    with STATE.lock:
        if STATE.picks:
            STATE.picks.pop()
    _autosave()
    return status()


class RemoveIn(BaseModel):
    player_id: str


@app.post("/api/pick/remove")
def remove_pick(body: RemoveIn) -> dict:
    """Take one specific player back off the board.

    Undo only reaches the last pick, and the mistake you notice is rarely the
    last one -- you look at your roster two rounds later and find someone you
    never meant to take. Removing him renumbers everything after, because
    overall pick numbers are positional.
    """
    st = _require()
    with STATE.lock:
        # Off the roster first. After the draft your team comes from ESPN, and
        # a man who arrived there by waiver was never in the pick log at all --
        # refusing to drop him because of that is answering the wrong question.
        on_roster = body.player_id in set(STATE.rosters.get(
            STATE.my_team_id, [])) or body.player_id in STATE.added
        if STATE.draft_complete and on_roster:
            if body.player_id in STATE.added:
                STATE.added.remove(body.player_id)
            elif body.player_id not in STATE.dropped:
                STATE.dropped.append(body.player_id)

        keep = [p for p in STATE.picks if p.get("player_id") != body.player_id]
        if len(keep) == len(STATE.picks):
            if STATE.draft_complete and on_roster:
                _autosave()
                return status()
            raise HTTPException(404, "that player is not in the pick history")
        for i, p in enumerate(keep, start=1):
            p["overall"] = i
            p["slot"] = slot_for_pick(
                (i - 1) // st.settings.n_teams + 1,
                (i - 1) % st.settings.n_teams + 1,
                st.settings.n_teams)
        STATE.picks = keep
    _autosave()
    return status()


@app.get("/api/status")
def status() -> dict:
    if STATE.settings is None:
        return {"configured": False}
    rnd, pick, overall = STATE.on_the_clock()
    mine = STATE.my_picks()
    upcoming = [p for p in mine if p >= overall]
    # YOUR DRAFT IS OVER WHEN YOUR TEAM IS FULL. The old test was whether the
    # league had used all its picks, which only ever fires when every seat's
    # picks are being recorded. Draft only your own men -- the whole point of
    # manual mode -- and the counter reaches 17 of 192 and stops, so the app
    # sat in a live draft forever: still offering "take these when it is your
    # turn" to a manager with no roster spots left.
    roster_full = (STATE.settings is not None
                   and len(STATE.my_ids) >= STATE.settings.roster_size)
    complete = (STATE.draft_complete
                or overall > STATE.settings.total_picks
                or roster_full)

    phase = "complete" if complete else ("live" if STATE.draft_started else "pre")
    warnings = []
    if STATE.espn and not STATE.slot_confirmed:
        # Same symptom, opposite causes, opposite fixes. Telling someone to
        # wait for ESPN when their cookie is actually stale is worse than
        # saying nothing -- they wait, and the seat never resolves.
        if STATE.swid_matched is False:
            warnings.append(
                f"Your SWID does not match any team in this league, so nobody "
                f"here is you — slot {STATE.my_slot} is a guess and the roster "
                f"below is whoever drafted there. Re-copy your cookies, or set "
                f"your picks by hand.")
        else:
            warnings.append(
                f"ESPN has not published the pick order yet, so draft slot "
                f"{STATE.my_slot} is a guess. Set it before you draft — a wrong "
                f"seat makes every survival probability wrong.")
    if complete:
        # No warning for a finished draft. The interface switches to the
        # post-draft view; saying it twice, once as an alert, was the app
        # treating a normal end state as a problem.
        pass

    return {
        "configured": True,
        "phase": phase,
        "slot_confirmed": STATE.slot_confirmed or STATE.espn is None,
        "draft_complete": complete,
        "warnings": warnings,
        "league_name": STATE.league_name,
        "describe": STATE.settings.describe(),
        "n_teams": STATE.settings.n_teams,
        "n_rounds": STATE.settings.n_rounds,
        "lineup": STATE.settings.lineup,
        "points_per_reception": STATE.settings.points_per_reception,
        "my_slot": STATE.my_slot,
        "platform": STATE.platform,
        "league_id": STATE.league_id,
        "saved_name": STATE.name,
        "my_picks": mine,
        "picks_traded": STATE.owned_picks is not None,
        "draft_time": STATE.draft_time,
        "seconds_to_draft": (STATE.draft_time - time.time())
                            if STATE.draft_time else None,
        "risk_tolerance": STATE.risk,
        "bench_tolerance": STATE.bench_risk,
        "round": rnd, "pick": pick, "overall": overall,
        "on_the_clock": STATE.is_my_turn(),
        "my_next_pick": upcoming[0] if upcoming else None,
        "picks_until_next": (upcoming[0] - overall) if upcoming else None,
        "picks_made": len(STATE.picks),
        "espn_connected": STATE.espn is not None,
        "last_sync": STATE.last_sync,
        "sync_error": STATE.sync_error,
        # EVERY pick, newest first, with the round it fell in — the feed pages
        # by round now and cannot do that from the last eight. `mine` reads the
        # flag recorded on the pick; deriving it from the seat was wrong the
        # moment ownership stopped being a property of the pick NUMBER.
        "recent": [
            {"overall": p["overall"], "name": p["name"], "slot": p["slot"],
             "round": (p["overall"] - 1) // STATE.settings.n_teams + 1,
             "mine": p["mine"] if "mine" in p
                     else (p.get("team_id") == STATE.my_team_id
                           if STATE.my_team_id is not None
                           else p["overall"] in set(STATE.my_picks()))}
            for p in STATE.picks[::-1]
        ],
    }


# ---------------------------------------------------------------------------
# The shortlist
# ---------------------------------------------------------------------------

def _logos() -> dict[str, str]:
    """Team nickname -> its logo. Loaded once; the badges do not change.

    A DEFENCE HAS NO FACE. It is a team, so its picture is a team logo, and
    nflverse publishes one per franchise. Relocations put three rows under
    "Rams" (STL, LA, LAR) and they all point at the same badge, so matching on
    the nickname is unambiguous even though the abbreviation is not.
    """
    global _LOGOS
    if _LOGOS is None:
        try:
            import nflreadpy as nfl

            t = nfl.load_teams()
            _LOGOS = {r["team_nick"].lower(): r["team_logo_espn"]
                      for r in t.iter_rows(named=True)
                      if r.get("team_nick") and r.get("team_logo_espn")}
        except Exception:
            _LOGOS = {}
    return _LOGOS


_LOGOS: dict[str, str] | None = None


def _headshots(b: pl.DataFrame) -> dict[str, str]:
    """Board id -> a picture of him. Three kinds of id, three sources.

    Skill players are keyed on gsis and ESPN serves portraits by ESPN id, so
    the URL cannot be built in the browser without shipping the crosswalk.
    Kickers and defences are keyed `espn-<id>` because neither has a gsis --
    which also means the ESPN id is right there in the key, and a kicker's
    portrait needs no crosswalk at all. Both were simply missing before: the
    map only ever had gsis keys, so every kicker and every defence in the app
    drew an empty grey square.
    """
    out: dict[str, str] = {}
    try:
        from fantasyedge.data import espn as espn_adp
        m = espn_adp.load().select(["gsis_id", "espn_id"]).drop_nulls()
        out = {r["gsis_id"]: HEADSHOT.format(espn_id=r["espn_id"])
               for r in m.iter_rows(named=True)}
    except Exception:
        pass

    logos = _logos()
    for r in b.filter(pl.col("player_id").str.starts_with("espn-")).iter_rows(
            named=True):
        pid = r["player_id"]
        if r["position"] == "DST":
            nick = (r["player_name"] or "").replace("D/ST", "").strip().lower()
            if nick in logos:
                out[pid] = logos[nick]
        else:
            out[pid] = HEADSHOT.format(espn_id=pid.removeprefix("espn-"))
    return out


@app.get("/api/suggestions")
def suggestions(n: int = 3, exclude: str = "",
                at: int | None = None) -> dict:
    """The names to take right now, with the reasoning that produced them."""
    st = _require()
    _, _, live = st.on_the_clock()
    overall = at or live
    if st.draft_complete or overall > st.settings.total_picks:
        # Recommending a 17th round of a 16-round draft is not a small
        # cosmetic problem -- it is the tool confidently answering a question
        # nobody asked.
        return {"suggestions": [], "note": ""}

    b = _board()
    skip = [x for x in exclude.split(",") if x]

    # PLANNING AHEAD HAS TO DROP THE MEN WHO WILL BE GONE. Asked what to target
    # in round four, the first version answered with the same three names as
    # round one -- correct in the sense that nobody has taken them in this
    # empty draft, and useless, because by pick 39 they are not a choice you
    # get to make. So when evaluating a FUTURE pick, anyone with less than a
    # one-in-ten chance of lasting that long is filtered out. The current pick
    # is never filtered: who is actually on the board is a fact, not a forecast.
    if at and at > live:
        b = b.with_columns(
            pl.struct(["ecr", "sd"]).map_elements(
                lambda r: survival_probability(r["ecr"], r["sd"],
                                               at - live, live),
                return_dtype=pl.Float64).alias("_reach")
        ).filter(pl.col("_reach") >= 0.10).drop("_reach")

    state = DraftState(settings=st.settings, my_slot=st.my_slot,
                       drafted=st.drafted_ids, my_roster=st.my_ids,
                       owned_picks=st.owned_picks,
                       at_overall=at if at and at != live else None)

    strength = None
    if st.my_ids:
        g = grade_roster(b.filter(pl.col("player_id").is_in(st.my_ids)),
                         st.settings, b)
        if g.get("score") and g.get("par"):
            strength = (g["score"] - 100) / 100 * g["par"] / 14.0

    rec = recommend(state, b, n=n, exclude=skip, risk_tolerance=st.risk,
                    bench_tolerance=st.bench_risk, roster_strength=strength)
    if not rec.height:
        return {"suggestions": [], "note": "nobody left"}

    extra = [c for c in ("rookie", "td_share", "rec_share") if c in b.columns]
    rec = rec.join(b.select(["player_id"] + extra), on="player_id", how="left")

    roster_pos = b.filter(pl.col("player_id").is_in(st.my_ids))["position"].to_list()
    needs = roster_needs(state, roster_pos)
    ctx = {
        "picks_until_next": rec["picks_until_next"][0],
        "needs": needs,
        "dropoff": dict(zip(rec["position"].to_list(), rec["dropoff"].to_list())),
        "target_vol": rec["target_vol_pct"][0],
    }
    shots = _headshots(b)
    rows = explain.annotate(rec, ctx)
    for r in rows:
        r["headshot"] = shots.get(r["player_id"])

    return {
        "round": int(rec["round"][0]),
        "pick": int(rec["pick"][0]),
        "overall": int(rec["overall"][0]),
        "picks_until_next": ctx["picks_until_next"],
        "target_vol_pct": ctx["target_vol"],
        "roster_risk": rec["roster_risk"][0],
        "needs": needs,
        "compare": explain.compare(rows),
        "suggestions": rows,
        "planning": bool(at and at != live),
        "live_overall": live,
        # (overall, round) pairs. The round is computed here because this is
        # where n_teams lives -- deriving it in the browser meant hardcoding 12
        # and silently mislabelling every pick in a 10-team league.
        "my_picks": [
            {"overall": p,
             "round": (p - 1) // st.settings.n_teams + 1}
            for p in st.my_picks()
        ],
    }


@app.get("/api/roster")
def roster() -> dict:
    st = _require()
    b = _board()
    mine = b.filter(pl.col("player_id").is_in(st.my_ids))
    if not mine.height:
        return {"grade": {}, "starters": [], "bench": []}

    grade = grade_roster(mine, st.settings, b)
    starters, bench = optimal_lineup(mine, st.settings, "projected_points")

    def rows(df):
        if not getattr(df, "height", 0):
            return []
        keep = [c for c in ("slot", "player_name", "position", "projected_points",
                            "vor", "season_p20", "season_p80", "player_id")
                if c in df.columns]
        return df.select(keep).to_dicts()

    # grade_roster hands back the lineup as DataFrames for CLI printing; those
    # are re-serialised below as plain rows, so drop the frames themselves.
    scalar = {k: v for k, v in grade.items() if not isinstance(v, pl.DataFrame)}
    return {"grade": scalar, "starters": rows(starters), "bench": rows(bench)}


@app.get("/api/teams")
def teams() -> dict:
    """Every roster in the league, so you can see what the room still needs."""
    st = _require()
    b = _board()
    by_slot: dict[int, list[dict]] = {}
    for p in st.picks:
        by_slot.setdefault(p["slot"], []).append(p)

    lookup = {r["player_id"]: r for r in b.select(
        ["player_id", "player_name", "position"]).iter_rows(named=True)}

    out = []
    for slot in range(1, st.settings.n_teams + 1):
        picks = by_slot.get(slot, [])
        counts: dict[str, int] = {}
        for p in picks:
            info = lookup.get(p.get("player_id") or "")
            pos = info["position"] if info else "?"
            counts[pos] = counts.get(pos, 0) + 1
        unfilled = {
            k: max(0, v - counts.get(k, 0))
            for k, v in st.settings.lineup.items() if k != "FLEX"
        }
        out.append({
            "slot": slot,
            "name": st.team_names.get(
                next((p["team_id"] for p in picks if p.get("team_id")), -1),
                f"Team {slot}"),
            "mine": slot == st.my_slot,
            "counts": counts,
            "unfilled": {k: v for k, v in unfilled.items() if v > 0},
            "players": [
                {"name": p["name"],
                 "position": (lookup.get(p.get("player_id") or "") or {}).get("position"),
                 "overall": p["overall"]}
                for p in picks
            ],
        })
    return {"teams": out}


@app.get("/api/board")
def board_view(pos: str | None = None, limit: int = 60) -> dict:
    """Best available, for when you want to look past the shortlist."""
    st = _require()
    b = _board().filter(~pl.col("player_id").is_in(st.drafted_ids))
    if pos:
        b = b.filter(pl.col("position") == pos.upper())
    shots = _headshots(b)
    keep = [c for c in ("player_id", "player_name", "position", "ecr", "vor",
                        "draft_rank", "projected_points", "season_p20",
                        "season_p80", "rookie")
            if c in b.columns]
    rows = b.sort("vor", descending=True, nulls_last=True).head(limit) \
            .select(keep).to_dicts()
    for r in rows:
        r["headshot"] = shots.get(r["player_id"])
    return {"players": rows}


@app.get("/api/analytics")
def analytics(depth: int = 10) -> dict:
    """Numbers behind the shortlist, shaped for charts.

    Two questions a ranked list cannot answer on its own: where the cliff at
    each position is, and how long the position will keep producing startable
    players relative to what the room still needs.
    """
    st = _require()
    b = _board().filter(~pl.col("player_id").is_in(st.drafted_ids))
    _, _, overall = st.on_the_clock()
    gap = None
    later = [p for p in st.my_picks() if p > overall]
    if later:
        gap = later[0] - overall - 1

    # How many bodies the room has already taken at each position.
    pos_by_id = dict(zip(_board()["player_id"].to_list(),
                         _board()["position"].to_list()))
    counts: dict[str, int] = {}
    for p in st.picks:
        pos = pos_by_id.get(p.get("player_id") or "")
        if pos:
            counts[pos] = counts.get(pos, 0) + 1

    tiers, scarcity = [], []
    for pos in ("QB", "RB", "WR", "TE"):
        pool = (b.filter(pl.col("position") == pos)
                 .sort("vor", descending=True, nulls_last=True))
        if not pool.height:
            continue
        rows = pool.head(depth).to_dicts()
        tiers.append({
            "position": pos,
            "players": [{
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "vor": round(r["vor"], 1) if r.get("vor") is not None else 0.0,
                "ecr": r.get("ecr"),
                "floor": round(r["season_p20"]) if r.get("season_p20") else None,
                "median": round(r["season_p50"]) if r.get("season_p50") else None,
                "ceiling": round(r["season_p80"]) if r.get("season_p80") else None,
                # Will he still be there at your next turn?
                "survives": (round(survival_probability(
                    r.get("ecr"), r.get("sd"), gap, overall), 2)
                    if gap is not None else None),
            } for r in rows],
        })
        startable = int(pool.filter(pl.col("vor") > 0).height)
        league_slots = st.settings.lineup.get(pos, 0) * st.settings.n_teams
        scarcity.append({
            "position": pos,
            "startable_left": startable,
            "league_slots": league_slots,
            "already_drafted": counts.get(pos, 0),
            "still_needed": max(0, league_slots - counts.get(pos, 0)),
        })

    return {"tiers": tiers, "scarcity": scarcity, "picks_until_next": gap}


@app.get("/api/adp")
def adp_ladder(limit: int | None = None,
               upcoming_only: bool = False) -> dict:
    """The draft board in ADP order, with what has already gone struck out.

    This is the ladder people actually read during a draft: who is next off
    the board, in the order the room will take them. Everything else here is
    analysis; this is the thing you glance at.
    """
    st = _require()
    # `limit` counts what comes BACK, and with upcoming_only that means
    # AVAILABLE players -- the slice below reaches past everyone already taken
    # to fill the quota, so the board never thins as the draft goes on.
    #
    # Unbounded by default, because two different needs got conflated here and
    # sizing for one broke the other. "Deep enough to never run dry" is about
    # five rounds; "deep enough to page through" is the whole board, and
    # picking the first number quietly capped browsing at sixty names. The
    # payload is ~110 KB at its very largest, on loopback, refetched only when
    # a pick actually lands -- so there is nothing to save by guessing.
    if limit is None:
        limit = st.settings.total_picks + 400
    b = _board()
    taken = set(st.drafted_ids)
    _, _, overall = st.on_the_clock()
    mine = set(st.my_picks())
    shots = _headshots(b)

    rows = b.sort("ecr", nulls_last=True).to_dicts()
    if upcoming_only:
        rows = [r for r in rows if r["player_id"] not in taken]

    out = []
    for r in rows[: limit + len(taken)]:
        if len(out) >= limit:
            break
        out.append({
            "player_id": r["player_id"],
            "player_name": r["player_name"],
            "position": r["position"],
            "ecr": r.get("ecr"),
            # ESPN's own board order, which is NOT its ADP. Shown so the
            # numbers here reconcile with the screen you are drafting from.
            "draft_rank": r.get("draft_rank"),
            "vor": round(r["vor"], 1) if r.get("vor") is not None else None,
            "rookie": bool(r.get("rookie")),
            "drafted": r["player_id"] in taken,
            "headshot": shots.get(r["player_id"]),
        })
    return {
        "platform": st.platform,
        "overall": overall,
        "my_next": min([p for p in mine if p >= overall], default=None),
        # Every pick you still hold, so the board can show WHERE they land.
        # A draft board sorted by ADP is a forecast of the order names come
        # off, so drawing your picks into it answers the question you actually
        # have between turns: who is likely to still be there when I am up.
        "my_upcoming": [
            {"overall": p, "round": (p - 1) // st.settings.n_teams + 1}
            for p in mine if p >= overall
        ],
        "players": out,
    }


# ---------------------------------------------------------------------------
# Saved leagues
# ---------------------------------------------------------------------------

class SaveIn(BaseModel):
    name: str


@app.get("/api/leagues")
def list_leagues() -> dict:
    """Everything you have saved, newest first. Drives the home screen."""
    return {"leagues": store.listing(), "active": STATE.league_id}


@app.post("/api/leagues/save")
def save_league(body: SaveIn) -> dict:
    """Rename this league.

    It is already saved -- every league is adopted the moment it is configured
    or connected, because that is when it starts being worth keeping. This
    only changes what it is called, and `force` because a rename does not move
    the fingerprint that normally decides whether a write is needed.
    """
    _require()
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "give the league a name")
    with STATE.lock:
        STATE.name = name
        if not STATE.league_id:
            STATE.league_id = store.new_id()
    _autosave(force=True)
    return status()


@app.post("/api/leagues/{league_id}/load")
def load_league(league_id: str) -> dict:
    """Resume a saved draft exactly where it stopped."""
    try:
        d = store.load(league_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc

    with STATE.lock:
        D.set_platform(d.get("platform") or "espn")
        STATE.settings = LeagueSettings(
            n_teams=d.get("n_teams", 12),
            lineup=d.get("lineup") or {"QB": 1, "RB": 2, "WR": 2, "TE": 1,
                                       "FLEX": 1, "K": 1, "DST": 1},
            points_per_reception=d.get("points_per_reception", 1.0),
            roster_size=d.get("roster_size", 16))
        STATE.platform = d.get("platform") or "espn"
        STATE.league_id = d.get("id") or league_id
        STATE.name = d.get("name") or ""
        STATE.my_slot = d.get("my_slot", 1)
        STATE.owned_picks = d.get("owned_picks")
        STATE.risk = d.get("risk", "combined")
        STATE.bench_risk = d.get("bench_risk", "aggressive")
        STATE.picks = d.get("picks") or []
        STATE.espn = d.get("espn")
        STATE.league_name = d.get("league_name") or ""
        STATE.team_names = {int(k): v for k, v in (d.get("team_names") or {}).items()}
        STATE.my_team_id = d.get("my_team_id")
        STATE.pinned = dict(d.get("pinned") or {})
        STATE.pinned_week = d.get("pinned_week")
        STATE.added = list(d.get("added") or [])
        STATE.dropped = list(d.get("dropped") or [])
        STATE.rosters = {}
        STATE.slot_confirmed = bool(d.get("slot_confirmed", True))
        STATE.draft_time = d.get("draft_time")
        STATE.draft_started = bool(STATE.picks)
        STATE.draft_complete = len(STATE.picks) >= STATE.settings.total_picks
        D._BOARD = None
    return status()


@app.delete("/api/leagues/{league_id}")
def delete_league(league_id: str) -> dict:
    try:
        store.delete(league_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if STATE.league_id == league_id:
        with STATE.lock:
            STATE.league_id = None
    return {"leagues": store.listing()}


@app.get("/api/player/{player_id}")
def player_profile(player_id: str) -> dict:
    """Everything known about one player, for the hover card."""
    st = _require()
    b = _board()
    hit = b.filter(pl.col("player_id") == player_id)
    if not hit.height:
        raise HTTPException(404, "no such player")
    r = hit.to_dicts()[0]

    _, _, overall = st.on_the_clock()
    later = [p for p in st.my_picks() if p > overall]
    gap = later[0] - overall - 1 if later else None
    surv = (survival_probability(r.get("ecr"), r.get("sd"), gap, overall)
            if gap is not None else None)

    # Where he sits inside his own position, on value.
    pool = (b.filter(pl.col("position") == r["position"])
             .sort("vor", descending=True, nulls_last=True))
    pos_rank = next((i for i, x in enumerate(pool["player_id"].to_list(), 1)
                     if x == player_id), None)

    return {
        "player_id": player_id,
        "player_name": r["player_name"],
        "position": r["position"],
        "headshot": _headshots(b).get(player_id),
        "rookie": bool(r.get("rookie")),
        "drafted": player_id in set(st.drafted_ids),
        "adp": r.get("ecr"),
        "draft_rank": r.get("draft_rank"),
        "pos_rank": pos_rank,
        "vor": round(r["vor"], 1) if r.get("vor") is not None else None,
        "projected_points": (round(r["projected_points"])
                             if r.get("projected_points") else None),
        "floor": round(r["season_p20"]) if r.get("season_p20") else None,
        "median": round(r["season_p50"]) if r.get("season_p50") else None,
        "ceiling": round(r["season_p80"]) if r.get("season_p80") else None,
        "expected_games": (round(r["expected_games"], 1)
                           if r.get("expected_games") else None),
        "td_share": r.get("td_share"),
        "rec_share": r.get("rec_share"),
        "survives": round(surv, 3) if surv is not None else None,
        "picks_until_next": gap,
        "platform": st.platform,
    }


@app.get("/api/build")
def build() -> dict:
    """What code this process is actually running, and for how long."""
    return {"build": BUILD, "started": STARTED,
            "uptime_hours": round((time.time() - STARTED) / 3600, 1)}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
