"""HTTP layer over the draft engine.

Everything here is a thin wrapper. The engine decides who to take, this
decides how to say it over JSON. Keeping it thin is deliberate -- the command
surface mirrors draft.py, so both front ends stay honest about running the
same code.

    uvicorn server.app:app --reload --port 8000
"""

from __future__ import annotations

import os
import sys
import threading
import time
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
from fantasyedge.draft import explain, report
from fantasyedge.draft.engine import (
    DraftState,
    recommend,
    roster_needs,
    survival_probability,
)
from fantasyedge.draft.session import grade_roster, optimal_lineup
from fantasyedge import trade
from fantasyedge.league import LeagueSettings, picks_for_slot, slot_for_pick
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
        if self.my_team_id is not None:
            return [p["player_id"] for p in self.picks
                    if p.get("player_id") and p.get("team_id") == self.my_team_id]
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
    }


def _autosave() -> None:
    """Persist after every mutation, so closing the tab costs nothing."""
    if STATE.league_id and STATE.settings is not None:
        try:
            store.save(STATE.league_id, _snapshot())
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
    known = set(b["player_id"].to_list())
    teams = STATE.settings.n_teams

    fresh = []
    for r in picks.iter_rows(named=True):
        pid = r.get("gsis_id")
        slot = slot_for_pick(r["round"], r["round_pick"], teams) \
            if r["round"] and r["round_pick"] else None
        if slot is None:
            slot = ((r["overall"] - 1) % teams) + 1
        fresh.append({
            "player_id": pid if pid in known else None,
            "espn_id": r.get("espn_id"),
            "name": r.get("player_name") or f"ESPN #{r.get('espn_id')}",
            "slot": slot,
            "team_id": r.get("team_id"),
            "overall": r["overall"],
        })
    with STATE.lock:
        STATE.picks = sorted(fresh, key=lambda p: p["overall"])
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


@app.get("/api/rosters")
def league_rosters() -> dict:
    """Who owns whom RIGHT NOW, not who was drafted.

    The draft log freezes the moment the draft ends; waivers, drops and trades
    all happen after it. Trade mode needs current ownership, and the ESPN
    payload has carried it in `mRoster` all along.
    """
    st = _require()
    if not st.espn:
        raise HTTPException(400, "no ESPN league connected")
    try:
        payload = espn_draft.fetch(**{k: v for k, v in st.espn.items()})
    except espn_draft.DraftUnavailable as exc:
        raise HTTPException(502, str(exc))

    r = espn_draft.rosters(payload)
    if not r.height:
        return {"teams": [], "note": "ESPN returned no rosters for this league"}

    b = _board()
    val = b.select(["player_id", "projected_points", "vor"])
    r = r.join(val, on="player_id", how="left")

    out = []
    for tid, grp in r.group_by("team_id"):
        team_id = tid[0] if isinstance(tid, tuple) else tid
        out.append({
            "team_id": team_id,
            "name": STATE.team_names.get(team_id, f"Team {team_id}"),
            "mine": team_id == STATE.my_team_id,
            "players": grp.sort("projected_points", descending=True, nulls_last=True)
                          .select(["player_id", "player_name", "position",
                                   "lineup_slot", "starting", "injury_status",
                                   "projected_points", "vor"]).to_dicts(),
        })
    return {"teams": sorted(out, key=lambda t: t["team_id"]),
            "as_of": time.time()}


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
    starters, bench = optimal_lineup(mine, st.settings)
    strength = report.positional_strength(mine, b, st.settings)
    dr = report.draft_report(st.picks, b, st.my_ids)
    words = report.summarise(strength, grade)
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
        "strength": strength,
        "byes": report.bye_conflicts(
            report.with_byes(mine, config.PRODUCTION_TARGET_SEASON),
            st.settings),
        "draft": dr,
        "strengths": words["strengths"],
        "weaknesses": words["weaknesses"],
    }


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------

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
        raise HTTPException(400, f"not on the board: {unknown}")

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

    v = trade.evaluate(mine, t.give, t.get, st.settings, b)
    d = _with_faces(v.as_dict(), b)

    # The roster as it would be afterwards, so the reasons are computed from
    # the same lineup the verdict came from rather than described from memory.
    after_ids = ([p for p in mine["player_id"].to_list() if p not in set(t.give)]
                 + list(t.get))
    dropped = {p["player_id"] for p in d.get("dropped") or []}
    after = b.filter(pl.col("player_id").is_in([x for x in after_ids
                                               if x not in dropped]))
    d.update(trade.explain.reasons(mine, after, st.settings, d))
    call, line = trade.explain.headline(d)
    d.update({"call": call, "summary": line})
    return d


@app.get("/api/trade/suggest")
def trade_suggest(per_team: int = 1, top: int = 8,
                  stance: str = "fair") -> dict:
    """Scan the league for deals that win for us and read as wins for them."""
    st = _require()
    if not STATE.espn:
        raise HTTPException(400, "connect an ESPN league to see other rosters")

    try:
        payload = espn_draft.fetch(**{k: v for k, v in STATE.espn.items()})
    except espn_draft.DraftUnavailable as exc:
        raise HTTPException(502, str(exc))

    r = espn_draft.rosters(payload)
    if not r.height:
        return {"offers": [], "note": "ESPN returned no rosters for this league"}

    b = _board()
    mine = b.filter(pl.col("player_id").is_in(
        r.filter(pl.col("team_id") == STATE.my_team_id)["player_id"].to_list()))
    if not mine.height:
        raise HTTPException(400, "could not identify your roster")

    others = {}
    for tid, grp in r.group_by("team_id"):
        team_id = tid[0] if isinstance(tid, tuple) else tid
        if team_id == STATE.my_team_id:
            continue
        sub = b.filter(pl.col("player_id").is_in(grp["player_id"].to_list()))
        if sub.height:
            others[team_id] = sub

    stamp = refresh.read_stamp()
    obs = refresh.observed() if stamp.got_stats else None
    week = stamp.week or 0

    prev = trade.suggest.THEIR_MIN_GAIN
    trade.suggest.THEIR_MIN_GAIN = trade.suggest.STANCE.get(stance, prev)
    try:
        offers = trade.suggest.across_league(
            mine, others, b, st.settings, names=STATE.team_names,
            observed=obs, through_week=week, per_team=per_team, top=top)
    finally:
        trade.suggest.THEIR_MIN_GAIN = prev

    return {"offers": [_with_faces(o.as_dict(), b) for o in offers],
            "through_week": week,
            "note": "" if offers else
                    "Nothing worth offering right now — every roster is priced "
                    "about right against yours."}


class CounterIn(BaseModel):
    give: list[str] = Field(default_factory=list)
    get: list[str] = Field(default_factory=list)
    team_id: int | None = None
    stance: str = "fair"


@app.post("/api/trade/counter")
def trade_counter(c: CounterIn) -> dict:
    """Better versions of the deal currently on the table.

    A search, not a rule. "Ask for one more small player" is the obvious move
    and usually the wrong one -- a small player is exactly what costs a roster
    spot and starts for nobody.
    """
    st = _require()
    if not STATE.espn:
        raise HTTPException(400, "connect an ESPN league to build counters")
    try:
        payload = espn_draft.fetch(**{k: v for k, v in STATE.espn.items()})
    except espn_draft.DraftUnavailable as exc:
        raise HTTPException(502, str(exc))

    r = espn_draft.rosters(payload)
    b = _board()
    mine = b.filter(pl.col("player_id").is_in(
        r.filter(pl.col("team_id") == STATE.my_team_id)["player_id"].to_list()))
    tid = c.team_id
    if tid is None:
        raise HTTPException(400, "which team are you trading with?")
    theirs = b.filter(pl.col("player_id").is_in(
        r.filter(pl.col("team_id") == tid)["player_id"].to_list()))
    if not mine.height or not theirs.height:
        raise HTTPException(400, "could not read both rosters")

    stamp = refresh.read_stamp()
    obs = refresh.observed() if stamp.got_stats else None
    offers = trade.suggest.counter(
        mine, theirs, b, st.settings, c.give, c.get, stance=c.stance,
        observed=obs, through_week=stamp.week or 0)
    return {"offers": [_with_faces(o.as_dict(), b) for o in offers]}


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
    with STATE.lock:
        # Marking a pick as yours records the pick NUMBER, which is what
        # ownership means once picks get traded.
        if p.mine and overall not in (STATE.owned_picks or st.my_picks()):
            STATE.owned_picks = sorted(set(st.my_picks()) | {overall})
        elif not p.mine and overall in (STATE.owned_picks or st.my_picks()):
            STATE.owned_picks = sorted(set(st.my_picks()) - {overall})
        STATE.picks.append({
            "player_id": hit["player_id"], "name": hit["player_name"],
            "slot": slot, "overall": overall, "team_id": None,
        })
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
        keep = [p for p in STATE.picks if p.get("player_id") != body.player_id]
        if len(keep) == len(STATE.picks):
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
    complete = STATE.draft_complete or overall > STATE.settings.total_picks

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
        warnings.append("This draft is finished. Nothing here is a live pick.")

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
        "recent": [
            {"overall": p["overall"], "name": p["name"], "slot": p["slot"],
             "mine": p["slot"] == STATE.my_slot}
            for p in STATE.picks[-8:][::-1]
        ],
    }


# ---------------------------------------------------------------------------
# The shortlist
# ---------------------------------------------------------------------------

def _headshots(b: pl.DataFrame) -> dict[str, str]:
    """gsis_id -> ESPN headshot url, via the crosswalk already in the board."""
    try:
        from fantasyedge.data import espn as espn_adp
        m = espn_adp.load().select(["gsis_id", "espn_id"]).drop_nulls()
        return {r["gsis_id"]: HEADSHOT.format(espn_id=r["espn_id"])
                for r in m.iter_rows(named=True)}
    except Exception:
        return {}


@app.get("/api/suggestions")
def suggestions(n: int = 3, exclude: str = "") -> dict:
    """The names to take right now, with the reasoning that produced them."""
    st = _require()
    _, _, overall = st.on_the_clock()
    if st.draft_complete or overall > st.settings.total_picks:
        # Recommending a 17th round of a 16-round draft is not a small
        # cosmetic problem -- it is the tool confidently answering a question
        # nobody asked.
        return {"suggestions": [],
                "note": "This draft is over. Every pick has been made."}

    b = _board()
    skip = [x for x in exclude.split(",") if x]

    state = DraftState(settings=st.settings, my_slot=st.my_slot,
                       drafted=st.drafted_ids, my_roster=st.my_ids,
                       owned_picks=st.owned_picks)

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
def adp_ladder(limit: int = 80, upcoming_only: bool = False) -> dict:
    """The draft board in ADP order, with what has already gone struck out.

    This is the ladder people actually read during a draft: who is next off
    the board, in the order the room will take them. Everything else here is
    analysis; this is the thing you glance at.
    """
    st = _require()
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
    """Name this draft so you can come back to it. Autosaves from here on."""
    _require()
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "give the league a name")
    with STATE.lock:
        STATE.name = name
        if not STATE.league_id:
            STATE.league_id = store.new_id()
    _autosave()
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
