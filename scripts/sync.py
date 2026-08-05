#!/usr/bin/env python
"""Pull the season forward. This is what the scheduler runs.

    scripts/sync.py                 # weekly window; no-ops if already fresh
    scripts/sync.py --force         # pull regardless
    scripts/sync.py --season 2025   # a specific season
    scripts/sync.py --status        # what is fresh, pull nothing

Exit codes matter here, because a scheduler is the only thing watching:

    0   fresh, or pulled successfully
    1   ran but got nothing at all -- worth looking at
    2   crashed
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fantasyedge.data import refresh  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh in-season data")
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--force", action="store_true",
                    help="pull even if the last one is still inside the window")
    ap.add_argument("--status", action="store_true", help="report and exit")
    a = ap.parse_args()

    if a.status:
        print(json.dumps(refresh.describe(), indent=2))
        return 0

    stamp = refresh.season(a.season, force=a.force)
    got = [r for r in stamp.results if r.get("ok")]

    for r in stamp.results:
        mark = "ok " if r.get("ok") else "-- "
        note = r.get("note") or ""
        print(f"{mark}{r['name']:<14} {r.get('rows', 0):>9,} rows  {note}")

    if stamp.week:
        print(f"\nthrough week {stamp.week} of {stamp.season}")

    if not got:
        # Normal in August: nothing is published until week one is played.
        # Still worth a non-zero exit so a scheduler log shows it.
        print("\nnothing pulled — the season has not started, or the feed moved")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"sync failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
