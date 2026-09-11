# FantasyEdge

Fantasy football valuation and risk engine for a real full-PPR ESPN league.
Treats players as assets: expected return, risk (floor, ceiling, availability),
and what a move is actually worth to *your* roster rather than in the abstract.

## What it actually is

Most of the engine is **measured statistics, not prediction**. Discount
factors, availability rates and outcome distributions are estimated from ten to
fifteen seasons of nflverse data, and a Monte Carlo season simulation turns
them into the answer to a specific question: what does this trade, claim or
pick do to the lineup you can field?

```
nflverse + ESPN  →  measured curves and tables  →  season simulation  →  FastAPI  →  React
```

Gradient-boosted trees (XGBoost) are in here, but it is worth being precise
about where: they produce the **rookie projections**, and they are used offline
in `models/train.py` to test whether a learned model beats the measured curves.
The live draft board does not call them. That was a finding, not an oversight.

The recurring lesson is methodological, and it is written into the modules that
learned it: several features that "worked" turned out to be conditioning on the
outcome. The per-game curve is keyed on **pre-season** rank for exactly this
reason — the version it replaced grouped players by where they *finished*, so
every RB8 in it had by construction neither busted nor got hurt.

## Three modes

| Mode | Question it answers |
|---|---|
| **Draft** | Who to take, given your seat, your roster and who survives to your next pick |
| **Trade** | What a deal does to the lineup you can field — not what the two piles add up to |
| **Waivers** | Who to claim, who to drop, and what it costs |

Plus a team view: your lineup, the league around you, and where you are strong
or thin.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[viz]"
cp .env.example .env        # only if you want cookies on disk rather than typed
```

## Pull the data

```bash
python scripts/pull_data.py            # everything (~2 GB with play-by-play)
python scripts/pull_data.py --no-pbp   # quick pass, ~1 min
```

Downloads nflverse to `data/raw/` as parquet and builds the player ID
crosswalk. Cached — safe to re-run. Writes `data/raw/manifest.json` with the
snapshot date; **cite that date in any reported result**, since nflverse
updates continuously during the season.

## Run it

```bash
./run.sh          # engine on :8000, interface on http://localhost:5173
```

Connect an ESPN league by its ID and the app reads that league's own team
count, lineup, PPR and roster rules. During a draft it polls every couple of
seconds and recomputes the moment the board moves — advice that arrives after
the pick is worse than none.

Private leagues need `espn_s2` and `SWID` cookies (DevTools → Application →
Cookies → espn.com). Paste them into the setup screen or put them in `.env`.
**They stay on your machine and are only ever sent to ESPN.** The server binds
to loopback and refuses anything else; that binding is the security control,
because there is no login and one process holds one league.

No ESPN league — a mock, Yahoo, Sleeper, a paper draft — works too: set the
league up by hand and type picks in as they happen. The terminal front end runs
the same engine:

```bash
python draft.py start --slot 10 --teams 12 --risk combined --bench-risk aggressive
python draft.py suggest
python draft.py take "Gibbs"
```

## Layout

```
fantasyedge/
├── config.py              every split decision, as data
├── league.py              league rules as a runtime value, not a constant
├── waiver.py              claims, drops, and what each is worth
├── data/
│   ├── pull.py            nflverse → parquet cache
│   ├── refresh.py         the weekly in-season pull, and what it got
│   ├── crosswalk.py       player ID spine (gsis_id)
│   ├── espn.py            ESPN ADP — the price the league drafts from
│   ├── espn_draft.py      the live draft and current rosters
│   ├── preseason.py       August box scores, scraped from ESPN
│   ├── depth.py           depth charts, injury tags, availability
│   └── stadiums.py        coordinates, roof, timezone
├── draft/
│   ├── engine.py          VOR, survival, pick-pair value, risk shaping
│   ├── explain.py         why this player, from the numbers that ranked him
│   └── session.py         persistent draft state, lineup, grading
├── trade/
│   ├── evaluate.py        paired-difference season simulation
│   ├── suggest.py         deals worth sending, scanned across the league
│   └── ask.py             what they would want back
├── models/
│   ├── pergame_curve.py   pre-season rank → what actually happened
│   ├── season_sim.py      rate × availability, simulated
│   ├── inseason.py        empirical Bayes: prior vs games played
│   ├── kdst.py            kickers and defences off the Vegas line
│   ├── rookie_risk.py     measured rookie downside
│   ├── train.py           XGBoost — offline evaluation
│   └── calibrate.py       is the predicted floor actually a floor?
└── diagnostics/report.py  where the model works and where it does not

server/app.py              HTTP over the engine
web/                       React + Tailwind interface
scripts/measure_*.py       the measurements the constants come from
```

## Data sources

All free, no API keys.

| Source | Use |
|---|---|
| [nflreadpy](https://github.com/nflverse/nflreadpy) | Stats, snaps, depth charts, injuries, schedules, Vegas lines |
| ESPN fantasy API | ADP, live draft, rosters, injury tags, headshots |
| ESPN public API | Preseason box scores — nflverse publishes none |
| [Open-Meteo](https://open-meteo.com/) | Historical + forecast weather by stadium |

ESPN ADP replaced FantasyPros consensus as the price source: in an ESPN league
everyone drafts off ESPN's board, so that is the real market price, and the two
disagree by enough to change picks.

nflverse data is CC-BY 4.0; FTN charting is CC-BY-SA 4.0.

## Status

Built and in use:

- Data pipeline, ID crosswalk, feature table, diagnostics harness
- Draft engine — VOR, survival, pick-pair opportunity cost, risk shaping
- Live ESPN sync and web interface
- Trade mode — verdict, league scan, counter-offers, "make this trade fair"
- Waiver mode — priced claims, drop costs, suggested moves
- Kickers and defences off the Vegas line; weekly streaming
- In-season repricing, weekly data pull, injury feed

Not built:

- Comps model (k-NN peer groups)
- Weekly matchup model for skill positions

Earlier versions of this file described an MCP server as the interface layer.
There is no MCP server; the interface is FastAPI plus React, and the plan
changed long before the README did.

## Licence

MIT — see [LICENSE](LICENSE). Use it, fork it, build on it. Issues and pull
requests are welcome but this is one person's league engine, so it is not
promised to be general.
