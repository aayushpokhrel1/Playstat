# Basketball Analytics + Parlay Optimizer — Architecture Plan

## 1. Goal

A dashboard you check before a night of NBA games that shows:
- Player/team stats in an easy-to-scan format
- Model-predicted stat lines (points/rebounds/assists) vs. sportsbook lines
- Where your model disagrees with the market (the "edge")
- A suggested parlay for a target payout (e.g. 2x), optimized for the *highest joint probability at that payout* — not a guaranteed win. Parlay math means payout and risk are linked; the optimizer's job is to find the least-bad combination for your target, not to break that relationship.

This builds directly on your existing resume project (API-Basketball → PostgreSQL → Tableau), extended with a live-data layer, an odds/lines feed, and a prediction + optimization layer.

---

## 2. System Overview

```
API-Basketball ──┐
                  ├──> Ingestion Layer ──> PostgreSQL ──> Feature Engineering ──> ML Models ──> Edge Calculator ──> Parlay Optimizer ──> Dashboard
Odds API      ────┘
```

---

## 3. Data Layer

### 3.1 Sources
- **API-Basketball** — box scores, player stats, schedules, injury reports (you already have integration experience here)
- **Odds API** (new) — sportsbook lines for player props (points/rebounds/assists over-under) and moneylines/spreads. This is the piece your original project didn't need since it wasn't betting-focused. Worth a quick search for current options (e.g. The Odds API) before picking one — pricing/coverage changes.

### 3.2 Refresh cadence
- **Historical backfill**: one-time load of past season(s) for model training
- **Daily**: rosters, injury reports, upcoming schedule
- **Game-day**: odds lines refreshed every 15–30 min as lines move; live box scores during games if you want in-game tracking later (v2 feature, not required for MVP)

### 3.3 Schema sketch (PostgreSQL)

```sql
teams(team_id, name, conference, pace, def_rating, ...)
players(player_id, name, team_id, position, ...)
games(game_id, date, home_team_id, away_team_id, status)

player_game_stats(          -- actual results, used for training + backtesting
  player_id, game_id, points, rebounds, assists, minutes, usage_rate
)

rolling_player_features(    -- computed, not raw
  player_id, as_of_date, pts_avg_5, pts_avg_10, reb_avg_5, ast_avg_5,
  opp_def_rating, rest_days, is_home, is_back_to_back
)

prop_lines(                 -- from odds API, snapshot per pull
  line_id, player_id, game_id, stat_type, line_value, over_odds, under_odds, pulled_at
)

model_predictions(
  player_id, game_id, stat_type, predicted_mean, predicted_std,
  prob_over, prob_under, model_version
)

edges(
  player_id, game_id, stat_type, model_prob, implied_prob, edge, side  -- 'over'/'under'
)

parlay_recommendations(
  parlay_id, created_at, target_payout, legs (jsonb), joint_prob, combined_odds
)
```

---

## 4. Feature Engineering

Per player, per upcoming game, computed from historical data:
- Rolling averages (last 5/10/15 games) for points, rebounds, assists, minutes
- Opponent defensive rating vs. player's position
- Pace of both teams (possessions per game — more possessions = more stat opportunities)
- Rest days / back-to-back flag
- Home vs. away
- Usage rate trend
- Injury report status (out/questionable teammates can inflate a player's usage)

---

## 5. Prediction Models

Start simple, iterate:

- **v1**: Gradient boosted trees (XGBoost or LightGBM) or Poisson/negative-binomial regression per stat type (points, rebounds, assists are count-like, so Poisson-family models are a reasonable statistical fit, not just linear regression)
- Output: a predicted mean **and** spread (std dev or quantiles) — you need the distribution, not just a point estimate, to compute `P(over line)` and `P(under line)`
- **Calibration check**: before trusting probabilities for edge detection, validate with a Brier score or reliability curve on held-out games. A model that's directionally right but poorly calibrated will produce false edges.

**Edge calculation:**
```
implied_prob = odds_to_probability(sportsbook_odds)   # remove the vig first
edge = model_prob - implied_prob
```
Only legs with a real, calibration-checked edge should feed the optimizer — this is the actual value-add of the ML layer, more so than the parlay itself.

---

## 6. Parlay Optimizer

Given: a set of candidate legs (player, stat, side, model_prob, odds) and a target payout multiplier (e.g. 2.0x):

1. Filter to legs with positive edge (or at least well-calibrated probability)
2. Search combinations of legs whose **combined odds** land near the target multiplier
3. Among combinations at that multiplier, rank by **joint probability** (highest = least risky path to that payout)
4. **Correlation warning**: legs from the *same game* (e.g., a player's points + that team's win) are not independent — treat same-game combos separately, or exclude them from the naive joint-probability multiplication, since it will overstate the true probability
5. Return top N combinations with their estimated joint probability, not just "the parlay"

This is a constrained search problem (think: subset selection with a target-product constraint), not something with a closed-form guarantee — worth treating it as backtestable, i.e., track how often model-flagged edges actually hit, over time.

---

## 7. Dashboard / Frontend

Built as a FastAPI backend (`api/`) + Next.js web dashboard (`web/`) — the "web app" option below is what got built; Tableau and the React Native port weren't pursued for this project.

| Option (considered) | Verdict |
|---|---|
| **Tableau** (reuse original) | Not used — not live/interactive enough for game-day use |
| **Web app** (Next.js) | **Built** — `api/main.py` serves teams/players/stats/predictions/edges/parlay-recommendations/backtest-history; `web/` is the Next.js frontend |
| **React Native Android app** | Not used for this project |

## 7.1 External consumers (Budgerr integration)

`GET /edges` and `GET /parlay-recommendations` are also consumed by a separate project, [Budgerr](https://github.com/aayushpokhrel1/Budgerr) (a personal budgeting app with betting baked in) — its bet quick-entry form pre-fills a leg's player/stat/line/side/odds straight from `/edges`. This is a one-way, read-only dependency: no shared database, no write access back into playstat, just an HTTP call from Budgerr's frontends. `CORSMiddleware` is configured (`CORS_ORIGINS` env var) specifically so Budgerr's browser-based frontend can call it directly.

---

## 8. Build Order — status

All 8 phases are built:

1. ✅ **Data pipeline**: schema + API-Basketball ingestion + historical backfill
2. ✅ **Odds integration**: odds API client + prop line ingestion (`ingestion/odds_client.py`, `odds_ingest.py`)
3. ✅ **Baseline model**: points model, full pipeline working end to end
4. ✅ **Calibration + edge detection**: `modeling/calibration.py`, `modeling/edges.py`
5. ✅ **Extend models**: rebounds, assists (`modeling/features.py`, `predict.py`, `train.py`)
6. ✅ **Parlay optimizer**: `optimizer/parlay.py`
7. ✅ **Dashboard**: FastAPI + Next.js, see Section 7
8. ✅ **Backtest loop**: `modeling/backtest.py`, results accumulate in `backtest_runs`

**Current data state**: `model_predictions` and `backtest_runs` have real historical data (the model has actually been trained and backtested). `prop_lines`, `edges`, and `parlay_recommendations` are empty — the code path is built and tested, but there's no live odds data flowing yet (season/odds-availability timing, not a bug). `/edges` will return real rows once odds ingestion actually runs against current lines.

**Calibration status**: `modeling/backtest.py`'s coverage checks originally found the q16 (16th-percentile) quantile model meaningfully miscalibrated — empirical coverage ran 25–33% against a nominal 16% target across all three stats, meaning `predicted_std` understated how often low outcomes actually happen. Diagnosed before fixing: tried five XGBoost hyperparameter configs, coverage barely moved, ruling out overfitting — the real cause is structural, no current feature signals the actual drivers of unusually low stat lines (foul trouble, blowouts, load management). Fixed with split-conformal calibration in `modeling/train.py`'s `fit_models` (a held-out calibration slice measures and corrects the raw quantile models' error, centralized in `predicted_std_from_quantiles` so `predict.py`/`calibration.py`/`backtest.py` all apply it identically). That got rebounds and points close (19.5%/23.9% coverage_16) but left **assists** badly miscalibrated at ~32.6% — traced to discreteness: assists is heavily zero-inflated, so 267/1134 calibration-slice residuals landed on exactly 0, an atom of tied values that swallowed the entire 10th–30th percentile band `np.quantile` needed to search. The plain quantile call just returned 0 (no correction) regardless of what correction was actually needed. Fixed by jittering residuals with `U(-0.5, 0.5)` noise before taking the quantile (averaged over 200 draws for stability) — `_jittered_quantile` in `modeling/train.py`, same centralized path so all three consumers pick it up. This helped all three stats, not just assists: points coverage_16 → 16.1% (right on the 16% nominal target), rebounds → 18.0%. Assists → 8.4%, which clears the `DEVIATION_WARNING_THRESHOLD` in `modeling/calibration.py` (was flagged before, isn't now) but has overshot to the other side of nominal rather than landing on it — a real improvement, not a full fix. Worth another pass (e.g. a non-uniform jitter shaped to the actual assists count distribution, or better features) if assists edges matter once live betting starts.

---

## 9. Tech Stack Summary

- **Backend**: Python (pandas, scikit-learn / XGBoost / LightGBM), FastAPI to serve predictions to a frontend
- **Database**: PostgreSQL
- **Orchestration**: simple scheduled scripts (cron) to start; Airflow only if this grows past a solo project
- **Frontend**: Next.js

---

## 10. Running it

The API runs as an always-on `launchd` service (`com.playstat.api`, configured outside this repo at `~/Library/LaunchAgents/`), not a manually-started dev server — it survives logout/reboot and restarts automatically on crash, same setup as Budgerr's backend. Logs go to `api.log`/`api.error.log` at the repo root (gitignored).

The project lives under `~/dev/playstat`, not `~/Documents` — `~/Documents` is iCloud-synced on this machine, which caused intermittent file-read deadlocks for `launchd`-spawned processes specifically.

The `com.playstat.api` service does **not** run with `--reload` — code changes to `api/` require a manual restart to take effect: `launchctl kickstart -k gui/$(id -u)/com.playstat.api`. Forgetting this step makes it look like a change didn't work when it's actually just not loaded yet.

---

## 11. Known Issues & Follow-ups

Everything below is a known, already-diagnosed gap — not a surprise to rediscover. Grouped by area so a fresh session (or a different person) can pick any of these up without needing the history that led here.

**Data / ingestion**
- **2023-24 backfill still filling in**: 264 of 1,310 finished games have box scores as of this writing. The daily `launchd` job (`com.playstat.backfill`, chained `backfill --only stats && modeling.features && modeling.backtest`) adds more automatically and self-disables via `launchctl unload` once every game has stats — no action needed, just time.
- **`prop_lines`/`edges`/`parlay_recommendations` are empty**: genuinely blocked on the NBA season starting (~October 2026) and `ingestion/odds_ingest.py` actually finding live events. All the code is built and tested against synthetic data; nothing here is unbuilt, it's unexercised.
- **The 2026-27 season likely won't load on the free API-Basketball plan** — already hit this exact wall with 2025-26 (free tier only covers 2022–2024). Will need a paid plan when that season needs backfilling, or another provider.
- **Feature gaps**: `player_game_stats.usage_rate` was never populated (API-Basketball's box score endpoint doesn't expose the underlying FGA/FTA/TOV the formula needs), there's no injury-report ingestion, and `teams.pace`/`teams.def_rating` are still NULL — `opp_def_rating` is a simple "opponent's points allowed" proxy, not a real pace-adjusted rating. All three are README §4 features that were never built, not features that broke.

**Modeling**
- **Assists calibration improved but not fully fixed** (see §8) — jittering calibration residuals before taking the quantile (to break the zero-inflated tie at residual=0) took coverage_16 from 32.6% to 8.4%, clearing the miscalibration warning but overshooting nominal (16%) in the other direction. A non-uniform jitter matched to the actual count distribution, or better features, is the next step if this matters once live betting starts.
- **Many predictions share an identical `predicted_mean`** across different players — e.g., 27 different players had the exact same predicted points value in one check. Very likely XGBoost routing players who lack rolling-average history (early in a season, or anyone without 5-10 prior games loaded yet) to the same leaf nodes, producing identical output. Not investigated further; worth a look once more of the season is backfilled and this either resolves itself or clearly doesn't.
- **XGBoost isn't seeded** (no `random_state` set anywhere) — exact MAE/calibration numbers will vary slightly between identical runs. Expected noise from the library's internal stochasticity, not a bug, but don't be alarmed if two `modeling.calibration` runs back-to-back don't match exactly.

**Dashboard / API**
- **No edge/parlay UI in the Next.js dashboard** — `web/` only has team/player browsing and predictions-vs-actuals (Phase 7 deliberately skipped this since `edges`/`parlay_recommendations` were empty at build time). Worth adding once real data exists to show.
- **No auth on any API endpoint** — fine for localhost-only dev, not fine before either Playstat's or Budgerr's API leaves personal/local use.
- **No backtest-trend chart** — only a handful of `backtest_runs` rows exist so far; a chart showing MAE/calibration over time is worth building once a couple weeks of daily runs have accumulated enough points to show a real trend.

**Budgerr integration**
- **Budgerr-side work hasn't started** — Playstat exposes `/edges`, `/parlay-recommendations`, and `/box-scores` (for auto-settlement), all verified working, but nothing in the Budgerr repo calls them yet. That's Budgerr's own build-order item 10, picked up whenever ready in that repo.

---

## 12. Session Notes (for whoever/whatever picks this up next)

This project's been built over a long-running Claude Code session that eventually hit the practical context-window limit. A few notes for next time, since that'll happen again on a project this size:

- **The context window is a model-level limit, not an interface one** — switching between Claude Code surfaces (terminal vs. a GUI wrapper) doesn't change how much it can hold. What actually helps is not relying on conversation history for anything durable.
- **§11 above exists for exactly this reason** — a fresh session should read "Known Issues & Follow-ups" first, rather than needing this session's full history to know where things stand.
- **Consider adding a `CLAUDE.md`** (doesn't exist yet) — Claude Code loads it automatically at the start of every session, which is a cheaper way to carry forward conventions/instructions than re-explaining them in chat each time. Keep it focused on *how to work in this repo*; leave the architecture/status narrative in this README.
- **`/compact`** (manually compact a long conversation) and **`/clear`** (reset the conversation, keep the project files) are both available in the terminal CLI if a session is getting unwieldy — better than losing everything by starting over, if there's context worth keeping.
- **The general pattern**: externalize durable state into files — this README in particular — rather than conversation memory. That's the actual fix for "long project, limited context," and it works the same regardless of which Claude Code surface is in use.

---

## Next Step

The build-order phases are all built. What's left is mostly data/ops and the follow-ups above, not new architecture: get current-season odds actually flowing through `ingestion/odds_ingest.py` so `prop_lines`/`edges`/`parlay_recommendations` stop being empty, work through §11's list as it becomes relevant, and decide whether/how to deploy this beyond localhost.
