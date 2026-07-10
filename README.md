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

Three options, not mutually exclusive:

| Option | Pros | Cons |
|---|---|---|
| **Tableau** (reuse original) | Familiar, resume-consistent | Not live/interactive for game-day use |
| **Web app** (Streamlit for fast MVP, or React/Next.js for a polished portfolio piece) | Live refresh, interactive, deployable | More build time |
| **React Native Android app** | You're already porting the dashboard into this — could be the natural home for the "check before tipoff" use case, on your phone | Depends on how far along that port is |

Given you're already mid-port on the React Native version, that's probably the most natural landing spot for the live/game-night view, with the web or Tableau version as the "deep dive" analysis layer.

---

## 8. Build Order (suggested phases)

1. **Data pipeline**: schema + API-Basketball ingestion + historical backfill
2. **Odds integration**: pick an odds API, ingest prop lines
3. **Baseline model**: start with points only, get the full pipeline working end to end
4. **Calibration + edge detection**: validate before trusting outputs
5. **Extend models**: rebounds, assists
6. **Parlay optimizer**: combinatorial search + correlation handling
7. **Dashboard**: wire predictions/edges into whichever frontend you pick
8. **Backtest loop**: track predicted vs. actual over time to see if the edge is real or noise

---

## 9. Tech Stack Summary

- **Backend**: Python (pandas, scikit-learn / XGBoost / LightGBM), FastAPI to serve predictions to a frontend
- **Database**: PostgreSQL
- **Orchestration**: simple scheduled scripts (cron) to start; Airflow only if this grows past a solo project
- **Frontend**: Streamlit (fastest MVP) → React/Next.js or React Native (polish/mobile)

---

## Next Step

Pick a phase from Section 8 and we can start building — Claude Code is the right tool for the actual implementation given the multi-file, multi-service nature of this (data pipeline, models, DB, frontend all as real code, not chat snippets).
