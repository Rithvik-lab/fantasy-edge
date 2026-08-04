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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import draft as D
from fantasyedge.data import espn_draft
from fantasyedge.draft import explain
from fantasyedge.draft.engine import DraftState, recommend, roster_needs
from fantasyedge.draft.session import grade_roster, optimal_lineup
from fantasyedge.league import LeagueSettings, picks_for_slot, slot_for_pick

app = FastAPI(title="FantasyEdge draft", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADSHOT = "https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png"


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
        self.draft_started: bool = False
        self.draft_complete: bool = False

    # -- derived ----------------------------------------------------------

    @property
    def drafted_ids(self) -> list[str]:
        return [p["player_id"] for p in self.picks if p.get("player_id")]

    @property
    def my_ids(self) -> list[str]:
        return [p["player_id"] for p in self.picks
                if p.get("player_id") and p.get("slot") == self.my_slot]

    def on_the_clock(self) -> tuple[int, int, int]:
        n = len(self.picks) + 1
        teams = self.settings.n_teams
        rnd = (n - 1) // teams + 1
        pick = (n - 1) % teams + 1
        return rnd, pick, n

    def is_my_turn(self) -> bool:
        rnd, pick, _ = self.on_the_clock()
        return slot_for_pick(rnd, pick, self.settings.n_teams) == self.my_slot


STATE = Draft()


def _require() -> Draft:
    if STATE.settings is None:
        raise HTTPException(400, "no draft configured; POST /api/league first")
    return STATE


def _board() -> pl.DataFrame:
    return D.board(STATE.settings)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LeagueIn(BaseModel):
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
        STATE.settings = LeagueSettings(
            n_teams=cfg.n_teams, lineup=lineup,
            points_per_reception=cfg.points_per_reception,
            roster_size=cfg.roster_size)
        STATE.my_slot = cfg.my_slot
        STATE.risk = cfg.risk_tolerance
        STATE.bench_risk = cfg.bench_tolerance
        STATE.picks = []
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
        STATE.league_name = info.name
        STATE.team_names = {t["id"]: t["name"] for t in info.teams}
        if cfg.adopt_settings:
            STATE.settings = LeagueSettings(
                n_teams=info.n_teams, lineup=info.lineup,
                points_per_reception=info.points_per_reception,
                roster_size=info.roster_size)
            D._BOARD = None
        tid = espn_draft.my_team_id(payload, swid)
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
    return status()


def _ingest(payload: dict) -> None:
    """Replace local pick history with ESPN's, which is authoritative."""
    info = espn_draft.league_info(payload)
    picks = espn_draft.picks(payload)
    total = STATE.settings.total_picks if STATE.settings else None

    with STATE.lock:
        STATE.last_sync = time.time()
        STATE.sync_error = None
        STATE.draft_started = bool(picks.height) or info.in_progress
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
    slot = st.my_slot if p.mine else slot_for_pick(rnd, pick, st.settings.n_teams)
    with STATE.lock:
        STATE.picks.append({
            "player_id": hit["player_id"], "name": hit["player_name"],
            "slot": slot, "overall": overall, "team_id": None,
        })
    return status()


@app.post("/api/undo")
def undo() -> dict:
    _require()
    with STATE.lock:
        if STATE.picks:
            STATE.picks.pop()
    return status()


@app.get("/api/status")
def status() -> dict:
    if STATE.settings is None:
        return {"configured": False}
    rnd, pick, overall = STATE.on_the_clock()
    mine = picks_for_slot(STATE.my_slot, STATE.settings.n_teams,
                          STATE.settings.n_rounds)
    upcoming = [p for p in mine if p >= overall]
    complete = STATE.draft_complete or overall > STATE.settings.total_picks

    phase = "complete" if complete else ("live" if STATE.draft_started else "pre")
    warnings = []
    if STATE.espn and not STATE.slot_confirmed:
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
                       drafted=st.drafted_ids, my_roster=st.my_ids)

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
                        "projected_points", "season_p20", "season_p80", "rookie")
            if c in b.columns]
    rows = b.sort("vor", descending=True, nulls_last=True).head(limit) \
            .select(keep).to_dicts()
    for r in rows:
        r["headshot"] = shots.get(r["player_id"])
    return {"players": rows}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
