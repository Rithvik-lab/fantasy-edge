# FantasyEdge

Fantasy football valuation and risk engine for a real full-PPR ESPN snake-draft
league. Treats players as assets: expected return (projected points), risk
(floor/ceiling and availability), and relative value against statistically
similar peers.

Three layers: ML models on historical NFL data → an MCP server exposing them as
tools → Claude Code as the conversational interface.

See [PLAN.md](PLAN.md) for the full spec.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[viz]"
cp .env.example .env        # add ESPN cookies when wiring the live league
```

## Pull the data

```bash
python scripts/pull_data.py            # everything (~2 GB with play-by-play)
python scripts/pull_data.py --no-pbp   # quick pass, ~1 min
```

Downloads nflverse 2010–2025 to `data/raw/` as parquet and builds the player ID
crosswalk. Cached — safe to re-run. Writes `data/raw/manifest.json` with the
snapshot date; **cite that date in any reported result**, since nflverse updates
continuously during the season.

## Layout

```
fantasyedge/
├── config.py            every split decision, as data
├── data/
│   ├── pull.py          nflverse → parquet cache
│   ├── crosswalk.py     player ID spine (gsis_id)
│   └── stadiums.py      coordinates, roof, timezone
├── models/              expected points, risk, comps
└── diagnostics/
    └── report.py        where the model works and where it doesn't
```

## Data sources

All free, no API keys.

| Source | Use |
|---|---|
| [nflreadpy](https://github.com/nflverse/nflreadpy) | Stats, snaps, injuries, NGS, schedules, Vegas lines |
| `load_ff_rankings()` | FantasyPros ECR via DynastyProcess — the market price |
| [Open-Meteo](https://open-meteo.com/) | Historical + forecast weather by stadium |
| `espn-api` | The live league (needs your own cookies) |

nflverse data is CC-BY 4.0; FTN charting is CC-BY-SA 4.0.

## Status

- [x] Repo, config, data pipeline, ID crosswalk, stadium table
- [x] Diagnostics harness
- [ ] Feature table
- [ ] Expected points model
- [ ] Risk model (quantile regression)
- [ ] Comps model
- [ ] MCP server
