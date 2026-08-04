"""Saved leagues, on disk.

A draft is not one sitting. You mock on Tuesday, draft for real on Sunday, and
run somebody else's league in between -- so a league is a thing you name, come
back to, and pick up mid-draft rather than a session that dies with the tab.

Everything lands in `data/processed/leagues/` as one JSON file per league,
autosaved on every pick. That directory is inside the gitignored data cache.

ON CREDENTIALS: a saved ESPN league keeps its espn_s2 and SWID so resuming
does not mean fishing them out of DevTools again. They are your login session,
so this is exactly as sensitive as the .env file they would otherwise live in
and no more -- local, gitignored, never sent anywhere but espn.com. Anyone who
would rather not have them on disk can leave the fields blank at setup and put
them in .env instead; the loader falls back to the environment.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from fantasyedge import config

DIR = config.PROCESSED / "leagues"


def _dir() -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    return DIR


def _path(league_id: str) -> Path:
    # Never let an id escape the directory.
    safe = "".join(c for c in league_id if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("bad league id")
    return _dir() / f"{safe}.json"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def save(league_id: str, payload: dict) -> dict:
    """Write a league. Returns the stored record."""
    record = dict(payload)
    record["id"] = league_id
    record["saved_at"] = time.time()
    _path(league_id).write_text(json.dumps(record, indent=2))
    return record


def load(league_id: str) -> dict:
    p = _path(league_id)
    if not p.exists():
        raise FileNotFoundError(f"no saved league {league_id}")
    return json.loads(p.read_text())


def delete(league_id: str) -> None:
    p = _path(league_id)
    if p.exists():
        p.unlink()


def listing() -> list[dict]:
    """Every saved league, newest first, without the bulky pick history."""
    out = []
    for p in sorted(_dir().glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        picks = d.get("picks") or []
        total = (d.get("n_teams") or 12) * (d.get("roster_size") or 16)
        out.append({
            "id": d.get("id") or p.stem,
            "name": d.get("name") or "Untitled league",
            "platform": d.get("platform") or "espn",
            "n_teams": d.get("n_teams"),
            "roster_size": d.get("roster_size"),
            "my_slot": d.get("my_slot"),
            "espn_league_id": (d.get("espn") or {}).get("league_id"),
            "picks_made": len(picks),
            "total_picks": total,
            "complete": len(picks) >= total,
            "saved_at": d.get("saved_at"),
        })
    return sorted(out, key=lambda r: r.get("saved_at") or 0, reverse=True)
