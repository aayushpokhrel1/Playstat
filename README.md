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

---

## Next Step

The build-order phases are done. What's left is more data/ops than code: get current-season odds actually flowing through `ingestion/odds_ingest.py` so `prop_lines`/`edges`/`parlay_recommendations` stop being empty, and decide whether/how to deploy this beyond localhost.
