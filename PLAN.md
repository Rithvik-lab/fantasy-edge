# Project FantasyEdge — Working Spec

Fantasy football valuation and risk engine. Applies relative valuation (comps),
volatility modelling, and simplified portfolio construction to a real full-PPR
ESPN snake-draft league.

This document supersedes the original project plan. Where it differs, this wins.
Amendments are marked **[REVISED]** with the reason.

---

## 1. Three pillars

| Pillar | Technique | Output |
|---|---|---|
| Relative valuation | k-NN comps + k-means archetypes | Peer group, price vs peers |
| Risk | Quantile regression (q20/q50/q80) + availability model | Floor, ceiling, volatility tier |
| Portfolio | Same-team/same-game correlation, mean-variance | Lineup for a risk tolerance |

Underlying all three: an **expected points** model (XGBoost regression) on
full-PPR scoring.

### [REVISED] k-NN for comps, not k-means

The original spec treated "k-means / k-NN" as interchangeable. They aren't.
Relative valuation compares an asset to a *named peer set*, not a bucket ID —
so k-NN drives valuation and k-means only supplies readable archetype labels.

Expect mediocre silhouette scores and don't chase them: fantasy features form a
continuum, not separated clusters. There is no natural gap between "possession
receiver" and "deep threat" — aDOT is a gradient. Cluster boundaries are for
communication; k-NN distances make the valuation calls.

### [REVISED] Quantile regression instead of predicting standard deviation

The original plan predicted week-to-week standard deviation. Four problems:

1. **Volatility scales with mean.** Predict raw sd and the "high volatility"
   list is just the "good players" list. Fix: feed predicted mean in as a
   feature so the model learns conditional volatility.
2. **Standard deviation punishes upside.** A 32-point week and a 2-point week
   are not equally bad. Finance solved this — downside deviation, the Sortino
   vs Sharpe distinction.
3. **Fantasy points are right-skewed**, because touchdowns arrive in +6 lumps.
   Normality assumptions don't hold.
4. `get_risk_profile()` promises "floor/ceiling estimate" — quantile regression
   produces that natively instead of deriving it from an assumed distribution.

XGBoost supports this directly: `objective="reg:quantileerror"`.

- **Floor** = q20, **ceiling** = q80
- **Boom/bust** = wide `q80 − q20` relative to q50
- **High floor** = high q20 for position

### [REVISED] Risk decomposes into two things

Performance volatility and availability are different risks that managers
handle differently — you play a boom/bust WR in the right matchup; you handcuff
an injury-prone RB. Blending them into one number destroys the distinction.

- **Performance volatility** — computed only over games at ≥40% snap share
  (`config.NORMAL_SNAP_SHARE_FLOOR`)
- **Availability** — P(misses game); a classification/count problem
  (`binary:logistic` or `count:poisson`), not regression

Season value then decomposes as `expected_games × expected_PPG`.

### Run this before building Pillar 2

**Is volatility even predictable?** For players with ≥10 games in consecutive
seasons, correlate weekly sd in season N against season N+1.

- r ≈ 0.4+ → real signal, build the model
- r ≈ 0.2 → weak but usable once conditioned on mean and TD dependency
- r ≈ 0.05 → **say so and simplify Pillar 2** to rules-based tiering

All three outcomes make a good write-up. The third arguably makes the best one.

---

## 2. Data

### [REVISED] `nflreadpy`, not `nfl_data_py`

Actively maintained successor. Returns **polars** DataFrames. Free, CC-BY 4.0
(FTN charting is CC-BY-SA 4.0 — attribute separately).

### [REVISED] ADP comes from `load_ff_rankings()`, not Sleeper

The original spec listed Sleeper for historical ADP. **Sleeper's public API has
no bulk or historical ADP endpoint** — only per-draft data for draft IDs you
already know.

Use `load_ff_rankings(type="all")` — DynastyProcess's archive of FantasyPros
expert consensus rankings. Free, no scraping, already ID-matched.

Caveat for the write-up: ECR is a *rankings consensus*, not observed ADP from
real drafts. Very good market proxy, but they diverge at the edges — which is
exactly where sleepers live.

### Coverage bounds — empirically probed, not assumed

| Dataset | Coverage | Tier |
|---|---|---|
| `player_stats`, `pbp`, `schedules` | 1999+ | core |
| `injuries`, `depth_charts`, `ff_opportunity` | 2010+ | core |
| `snap_counts` | 2013+ (2012 validates but returns 0 rows) | core |
| `nextgen_stats`, `participation` | **2016+** | gated |
| `pfr_advstats` | **2018+** | gated |
| `ftn_charting` | **2022+** | in-season only |

**This is why feature tiering exists.** A feature can't exist for a training row
predating its source. Letting XGBoost's null handling paper over it is worse
than it looks — "missing" correlates perfectly with era, so the model learns
*era* instead of football and still validates well.

`load_ff_opportunity()` deserves attention: expected fantasy points given
opportunity. Actual-minus-expected is the best regression-to-mean signal
available.

### ID crosswalk

`load_ff_playerids()` is the spine. Everything joins through `gsis_id`.

Measured coverage among fantasy-relevant players (50+ PPR points since 2020):
**712 of 713**, with `espn_id` and `fantasypros_id` both present. The 69.9%
`fantasypros_id` rate across the full 3,345-player table is obscure historical
players who never had an ADP.

`crosswalk.audit_join()` returns a match rate with every join — a silent 60%
join must not reach a feature table.

### Build ourselves (small, one-time, where the edge is)

- **Stadium coordinates** — done, `data/stadiums.py`. Enables weather + travel.
- **OC/HC by team-season** — ~320 rows. Coordinator turnover is a top-tier
  volatility predictor and **no free source has it**.
- **Injury history** — derived from rosters vs player_stats.
- **Vacated targets/touches** — prior-season share now off the roster.

### External

- **[Open-Meteo](https://open-meteo.com/)** — free, no key, hourly archive to
  1940, 10k calls/day. Wind gusts matter; temperature and rain mostly don't.
- **`espn-api`** — live league. Cookies in `.env`, never committed. They expire.

### Not available

True observed ADP (ECR proxy instead) · NFL player props (The Odds API needs
the $99/mo Business tier) · coverage schemes · route participation.

---

## 3. Train / validate / test

**Feature year ≠ label year.** A row predicting season N uses season N−1
features. Raw data starts 2010 so label-year 2014 has multi-year lookback.

| Seasons | Role |
|---|---|
| 2014–2022 | Train |
| 2018–2023 | Walk-forward validation (6 folds) |
| **2024** | Sealed test #1 |
| **2025** | Sealed test #2 |

Encoded in `config.VALIDATION_FOLDS`; `config.assert_no_leakage()` guards it.

Two sealed seasons don't buy a retry — testing, tuning, then re-testing burns
both. They buy a **stability estimate**: 3.9 and 4.1 is a real model; 3.6 and
5.2 is luck you'd never have caught with one season.

### After the config is locked

```
fit 2014–2023  →  predict 2024   → test #1
fit 2014–2024  →  predict 2025   → test #2
fit 2014–2025  →  predict 2026   → production, loaded by MCP
```

Two artifacts, versioned separately: `*_eval.pkl` produces the honest number,
`*_prod.pkl` is what the MCP server loads.

### Leakage traps

- Features must be knowable before kickoff. Prior-season stats, age, schedule:
  fine. Anything from season N itself: fatal, and it validates *beautifully*.
- **Rookies have no prior season.** Scope Model 1 to ≥1 prior season; handle
  rookies separately via draft capital + combine. Defer past the draft.
- 2020 is the COVID fold — decide how to treat it *before* seeing results.

Scale: ~2,500–3,500 player-seasons; ~1,800–2,400 after the ≥10-game volatility
filter. Small. Hence `max_depth` 3–6 and caring about fold variance.

---

## 4. Features

**Core (2014+)** — target share, snap %, RZ touches/targets, air yards, team
pass rate, age, games played, prior-season expected points.

**Volatility-specific** — these are where the risk model earns its keep:

- **TD dependency** — share of points from TDs. Likely the strongest single
  volatility predictor; TDs are the lumpiest and least sticky scoring event.
- **Reception share of points** — in **full PPR this is the floor mechanism**.
  8 catches for 55 yards is 13.5 points with no TD.
- **aDOT** — deep threats are boom/bust by construction.
- **Weekly stdev of prior-season target share** — usage consistency predicts
  output consistency. Leakage-safe.
- Role concentration, position, implied team total.

**Context (schedule-derived, season grain)** — `n_outdoor_games`,
`pct_dome_games`, `mean_opponent_pass_defense_epa`, `n_short_week_games`,
`total_travel_miles`. Schedule releases in May, so all leakage-safe.

**Vegas** — implied team total = `(total/2) − (spread/2)`, from `spread_line`
and `total_line` in `load_schedules()`. Best game-script feature available,
already in our data.

### Grain

Weather, opponent, rest are **per-game**. Draft mode is **per-season**. They
enter as schedule-derived aggregates at season grain and directly at week
grain. Two different models with different row grains — trying to serve both
from one is a trap.

### Defense-vs-position warning

The most abused stat in fantasy. A defense that faced Kelce, Bowers, and
LaPorta looks terrible against TEs — that's not a property of the defense.
Opponent-adjust and shrink toward league mean, or don't use it. Unadjusted DvP
is worse than no feature, because it's confidently wrong.

### Discipline

~3,000 rows. Add features in **blocks**, measure against walk-forward, drop
what doesn't earn its slot. SHAP-check that each feature helps for a reason
that makes football sense — one that helps incoherently will stop helping.

---

## 5. Diagnostics

`fantasyedge/diagnostics/report.py`. A single MAE says nothing actionable.

| Function | Question |
|---|---|
| `segment_errors()` | Which slice is worst? **Read `bias`** — a systematically under-rated slice is a missing feature, not noise |
| `tier_errors()` | Accurate on early rounds but wild on late ones is backwards for sleeper detection |
| `worst_misses()` | Read by name. Shared cause = missing feature |
| `quantile_calibration()` | Is the floor a floor? If 8% of actuals fall below q20, every risk claim is wrong |
| `fold_stability()` | Great on 3 folds and bad on 3 is fragile, not good |
| `value_gap_hit_rate()` | **When model and market disagreed, who won?** 50% = no edge over ADP regardless of MAE |
| `shap_summary()` | Is it using sensible signals? |

The loop: segment → attribute → add a targeted feature → re-check stability.

`value_gap_hit_rate()` is the one that matters most. A model can beat ADP on
MAE and still be useless for picks.

---

## 6. MCP server

**No Anthropic API key, no separate billing.** MCP is an open protocol; the
server is a local program with no LLM dependency. Claude Code is the client and
is covered by the existing subscription. Runs over stdio — no network.

Built with **FastMCP** (`@mcp.tool()`, stdio default). Registration:

```bash
claude mcp add fantasyedge -- /path/to/.venv/bin/python -m fantasyedge.mcp_server
```

Claude Desktop alternative: `claude_desktop_config.json`, or package as `.mcpb`.

Six tools per the original spec: `get_draft_sleepers`, `get_waiver_targets`,
`evaluate_trade`, `get_player_comps`, `get_risk_profile`, `optimize_lineup`.

Wrap every ESPN call so an unofficial-endpoint change degrades one tool rather
than crashing the server.

---

## 7. Open items

- **Replacement level / VOR** — the original spec has no way to compare across
  positions. A 15-point WR and a 15-point TE are not equally valuable. Needs
  league size and starting lineup (`config.LEAGUE_SIZE` is a placeholder —
  confirm the real league).
- **Draft board UI** — conversational MCP is the spec; a scannable board is
  worth half a day given ~90 seconds per pick at a live draft.
- **Bye weeks** — not in any pillar yet.

## 8. Honesty notes for the write-up

- Relative valuation, volatility modelling, and a **simplified** portfolio step
  — not institutional quant finance. The specific claim is stronger.
- Correlation is deliberately scoped to same-team/same-game, not a full N×N
  covariance matrix. State it plainly.
- ESPN's fantasy API is reverse-engineered, not public.
- ECR is a rankings consensus, not observed ADP.
- Report the sealed-test number whatever it says. If the volatility model
  underperforms validation, that's a finding.
