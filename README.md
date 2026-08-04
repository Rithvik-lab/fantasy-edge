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

## Draft day

```bash
./run.sh          # engine on :8000, interface on http://localhost:5173
```

Connect an ESPN league by its ID and the app reads that league's own team
count, lineup and PPR, then polls the live draft every 2.5 seconds. Picks
land on their own, and the shortlist is recomputed the instant the board
moves — so a name that just went is never still sitting on screen. That is
the whole point: advice that arrives after the pick is worse than none.

Private leagues need `espn_s2` and `SWID` cookies (DevTools → Application →
Cookies → espn.com). Put them in `.env` or paste them into the setup screen;
they stay on your machine and are only ever sent to ESPN.

No ESPN league — a mock, Yahoo, Sleeper, a paper draft — works too: set the
league up by hand and type picks in as they happen.

The terminal front end is unchanged and runs the same engine:

```bash
python draft.py start --slot 10 --teams 12 --risk combined --bench-risk aggressive
python draft.py suggest
python draft.py take "Gibbs"
```

## Layout

```
fantasyedge/
├── config.py            every split decision, as data
├── data/
│   ├── pull.py          nflverse → parquet cache
│   ├── crosswalk.py     player ID spine (gsis_id)
│   ├── espn.py          ESPN ADP — the price the league drafts from
│   ├── espn_draft.py    the live draft, read as it happens
│   └── stadiums.py      coordinates, roof, timezone
├── draft/
│   ├── engine.py        VOR, survival, pick-pair value, risk shaping
│   ├── explain.py       why this player, from the numbers that ranked him
│   └── session.py       persistent draft state, lineup, grading
├── models/
│   ├── pergame_curve.py pre-season rank → what actually happened
│   ├── rookie_risk.py   measured rookie downside
│   └── season_sim.py    rate × availability
└── diagnostics/
    └── report.py        where the model works and where it doesn't

server/app.py            HTTP over the engine
web/                     React + Tailwind interface
```

## Data sources

All free, no API keys.

| Source | Use |
|---|---|
| [nflreadpy](https://github.com/nflverse/nflreadpy) | Stats, snaps, injuries, NGS, schedules, Vegas lines |
| `load_ff_rankings()` | FantasyPros ECR via DynastyProcess — the market price |
| [Open-Meteo](https://open-meteo.com/) | Historical + forecast weather by stadium |
| ESPN fantasy API | ADP, live draft, headshots (cookies only for private leagues) |

nflverse data is CC-BY 4.0; FTN charting is CC-BY-SA 4.0.

## Status

- [x] Repo, config, data pipeline, ID crosswalk, stadium table
- [x] Diagnostics harness
- [x] Feature table (week grain, 128,767 rows)
- [x] Expected points model — blends with market at 30-35%
- [x] Risk model (quantile regression), discrimination 0.7526
- [x] Draft engine — VOR, survival, pick-pair opportunity cost
- [x] Live ESPN draft sync + web interface
- [ ] Comps model (k-NN peer groups)
- [ ] Waiver mode, trade evaluator, weekly in-season refresh
- [ ] MCP server
