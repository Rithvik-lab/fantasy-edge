#!/usr/bin/env python
"""Pull every nflverse dataset into data/raw/ and build the ID crosswalk.

    python scripts/pull_data.py              # everything, including pbp
    python scripts/pull_data.py --no-pbp     # quick pass, skips the big one
    python scripts/pull_data.py --force      # ignore the cache and re-download
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from fantasyedge import config  # noqa: E402
from fantasyedge.data import crosswalk, pull  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pbp", action="store_true", help="skip play-by-play")
    ap.add_argument("--force", action="store_true", help="re-download cached files")
    args = ap.parse_args()

    config.assert_no_leakage()

    lo, hi = config.RAW_SEASON_START, config.RAW_SEASON_END
    print(f"Pulling nflverse {lo}-{hi} -> {config.RAW}\n")

    pull.pull_all(force=args.force, include_pbp=not args.no_pbp)

    print("\nBuilding ID crosswalk...")
    cw = crosswalk.build()
    crosswalk.save(cw)
    print(f"  {cw.height:,} players ({', '.join(config.MODELED_POSITIONS)})\n")
    print(crosswalk.coverage_report(cw))

    info = pull.snapshot_info()
    print(f"\nSnapshot {info['snapshot_date']} (nflreadpy {info['nflreadpy_version']})")
    print("Cite this date in any reported result — nflverse updates continuously.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
