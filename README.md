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

**Auth**: when `AUTH_ENABLED=true` on the API, every endpoint requires an `X-API-Key` header matching one of the keys in `PLAYSTAT_API_KEYS` (comma-separated `name:key` pairs — Budgerr gets its own named key, e.g. `budgerr:<key>`, so it can be revoked independently of the dashboard's). CORS already allows the header (`allow_headers=["*"]`), so preflighted browser requests work. Budgerr has two options for supplying it: (a) embed the key in its browser frontend — acceptable for strictly personal/local use, but the key is visible to anyone who can open devtools; or (b) **recommended**: proxy Playstat calls through Budgerr's own backend, which attaches the key server-side and keeps it out of the browser entirely.

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

**Current data state**: every table now has real data except `parlay_recommendations`. `prop_lines` has live MLB lines (2026-07-13, ~1,000/pull across 13 stat types); `model_predictions` has both historical NBA backtest rows and **live MLB predictions for upcoming games** (`modeling/predict_upcoming.py`); `edges` has its first real rows (174, all currently in one game — see below). `parlay_recommendations` stays empty for a structural reason: during the All-Star break the books only posted props for a handful of far-out series, and only one lined game falls inside the prediction horizon — the optimizer's same-game exclusion correctly refuses single-game parlays. Once the season resumes (July 17) the daily job gets a full nightly slate and cross-game 2x parlays become findable. Two calibration lessons from the first real edges run: (1) a Gaussian CDF on 0.5-line count markets manufactured positive edges on nearly every over — `modeling/edges.py` now uses a Poisson for predicted means < 5; (2) after de-vigging, one side of every line has edge ≥ 0 *by construction*, so the parlay optimizer now demands `--min-edge` (default 3%) rather than edge > 0.

**Calibration status**: `modeling/backtest.py`'s coverage checks originally found the q16 (16th-percentile) quantile model meaningfully miscalibrated — empirical coverage ran 25–33% against a nominal 16% target across all three stats, meaning `predicted_std` understated how often low outcomes actually happen. Diagnosed before fixing: tried five XGBoost hyperparameter configs, coverage barely moved, ruling out overfitting — the real cause is structural, no current feature signals the actual drivers of unusually low stat lines (foul trouble, blowouts, load management). Fixed with split-conformal calibration in `modeling/train.py`'s `fit_models` (a held-out calibration slice measures and corrects the raw quantile models' error, centralized in `predicted_std_from_quantiles` so `predict.py`/`calibration.py`/`backtest.py` all apply it identically). That got rebounds and points close (19.5%/23.9% coverage_16) but left **assists** badly miscalibrated at ~32.6% — traced to discreteness: assists is heavily zero-inflated, so 267/1134 calibration-slice residuals landed on exactly 0, an atom of tied values that swallowed the entire 10th–30th percentile band `np.quantile` needed to search. The plain quantile call just returned 0 (no correction) regardless of what correction was actually needed. Fixed by jittering residuals with `U(-0.5, 0.5)` noise before taking the quantile (averaged over 200 draws for stability) — `_jittered_quantile` in `modeling/train.py`, same centralized path so all three consumers pick it up. This helped all three stats, not just assists: points coverage_16 → 16.1% (right on the 16% nominal target), rebounds → 18.0%. Assists → 8.4%, which clears the `DEVIATION_WARNING_THRESHOLD` in `modeling/calibration.py` (was flagged before, isn't now) but has overshot to the other side of nominal rather than landing on it — a real improvement, not a full fix. Worth another pass (e.g. a non-uniform jitter shaped to the actual assists count distribution, or better features) if assists edges matter once live betting starts.

**MLB calibration status (v2, discrete distributions)**: everything in the previous paragraph now applies only to the NBA stats. The 13 MLB count stats no longer use the Gaussian + quantile + conformal machinery at all — each gets a discrete predictive distribution end to end (`xgb_nbinom_{stat}_v2`): an XGBoost `count:poisson` mean model plus a per-stat NB2 overdispersion parameter r estimated by method of moments on a held-out calibration slice (`fit_discrete` in `modeling/train.py`); `predicted_std` in `model_predictions` is that distribution's true std (`sqrt(mu)` Poisson / `sqrt(mu + mu²/r)` NB), and `modeling/distributions.py` is the single shared reconstruction (`std² ≤ mean → Poisson(mu)`, else NB with `r = mu²/(std²−mu)`) used by `edges.py` and all evaluation code. `modeling/calibration.py` evaluates the discrete stats with a randomized PIT uniformity check plus discrete-percentile coverage compared against the distribution's *own expected* coverage (nominal 16%/84% are structurally unreachable on lumpy counts — `coverage_16`/`coverage_84` in `backtest_runs` overshoot nominal by construction and should be read against the printed model-expected values). The head-to-head acceptance gate lives in `modeling/eval_discrete.py` (old Gaussian+Poisson-stopgap vs new discrete P(over), Brier/log-loss per stat on the held-out slice); at cutover the new model was equal-or-better on Brier and MAE for all 13 stats (mean Brier 0.1511 → 0.1495, biggest win on overdispersed total_bases 0.2426 → 0.2311); the one regression was outs_recorded log-loss (0.1786 → 0.1827, Brier still better) — its PIT std of 0.250 says the NB(r≈48) is slightly too wide for that stat, a candidate for the mean-binned-r refinement. Caveat: no historical prop line joins to a finished game yet, so that gate used synthesized lines (anchored at each player's rolling average, clamped to real book-line ranges) — re-run it against real lines once settled `prop_lines` accumulate.

**First settled paper-trading results (recorded 2026-07-18)**: the 2026-07-17 slate (the season-resume slate) is the first to finish and be scored by `modeling/settle.py`, so the ledger has its first real numbers. **One slate only — treat every figure below as noisy and provisional, not a verdict; a week-plus is needed before any of it is trustworthy.** Player-prop **edges** (bets > 3%): 541–315, +6.59u on 856 unit bets = **+0.8% ROI** (essentially break-even). Player-prop **parlays**: 6–1, +3.32u on 7 bets (+47.5% ROI is pure small-sample noise — these were the heavy-favorite SB-under parlays; favorites usually don't steal, but the juice makes them thin regardless). By-stat edge ROI is where the signal-if-any lives, but at ~1 day and n≈14–116 per stat it's mostly noise: **losing** on the high-frequency pitcher/discipline stats (batter_strikeouts −20.7%, walks −15.2%, outs_recorded −14.6%, pitcher_strikeouts −14.0%), **winning** on the offensive counting stats (runs +14.0%, home_runs +12.3%, rbis +8.0%, total_bases +6.1%); stolen_bases 74–9 but only +2.4% (juice). The losing-on-Ks/walks pattern is *consistent with* the player-prop shrinkage bias (§11 — the model does worst where "predict the league average" hurts most against a sharp market), which is the evidence base for the team-market pivot (§14.3 / the NRFI+F5 build) — but it is not proof, and crucially the system is **~break-even, not bleeding**, so the pivot is about finding *better* edges, not fleeing a losing one. This is also the first real settled data for the §14.1 real-line `eval_discrete` re-run (previously blocked on synthesized lines).

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

### Auth

Both halves are **off by default** — with the env vars unset, everything behaves exactly as before.

- **API** (`.env` / launchd env): set `AUTH_ENABLED=true` and `PLAYSTAT_API_KEYS=dashboard:<key>,budgerr:<key>` (comma-separated `name:key` pairs; names are per-consumer labels for revocation). Every endpoint then requires a matching `X-API-Key` header and returns 401 otherwise.
- **Dashboard** (`web/.env.local`, gitignored — see `web/.env.local.example`): `PLAYSTAT_API_KEY` (the dashboard's key, attached server-side by `web/app/lib/api.ts`; it never reaches the browser), `DASHBOARD_USER`, `DASHBOARD_PASSWORD_HASH`, and `SESSION_SECRET` (random hex; unset = login disabled). Generate the password hash with `node web/scripts/hash-password.mjs <password>` (format `scrypt$<salt_hex>$<hash_hex>`) and a secret with `node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"`. Sessions are HMAC-signed httpOnly cookies (`playstat_session`, 7 days), verified by `web/proxy.ts` (Next 16's renamed middleware); log out via the home-page link (POST `/api/logout`).

---

## 11. Known Issues & Follow-ups

Everything below is a known, already-diagnosed gap — not a surprise to rediscover. Grouped by area so a fresh session (or a different person) can pick any of these up without needing the history that led here.

**Data / ingestion**
- **2023-24 backfill still filling in**: 264 of 1,310 finished games have box scores as of this writing. The daily `launchd` job (`com.playstat.backfill`, chained `backfill --only stats && modeling.features && modeling.backtest`) adds more automatically and self-disables via `launchctl unload` once every game has stats — no action needed, just time.
- **`prop_lines` now has live MLB data** (2026-07-13); `edges`/`parlay_recommendations` remain empty until MLB modeling exists (§13.1) or the NBA season starts (~October 2026) and gives the NBA models lines to compare against. The odds→lines path is now exercised with real data; the lines→edges→parlay path still isn't.
- **The 2026-27 season likely won't load on the free API-Basketball plan** — already hit this exact wall with 2025-26 (free tier only covers 2022–2024). Will need a paid plan when that season needs backfilling, or another provider.
- **Feature gaps**: `player_game_stats.usage_rate` was never populated (API-Basketball's box score endpoint doesn't expose the underlying FGA/FTA/TOV the formula needs), there's no injury-report ingestion, and `teams.pace`/`teams.def_rating` are still NULL — `opp_def_rating` is a simple "opponent's points allowed" proxy, not a real pace-adjusted rating. All three are README §4 features that were never built, not features that broke.

**Modeling**
- **MLB coverage_16 ≈ 0% — ADDRESSED (v2 discrete distributions, 2026-07-14)**: the structural diagnosis stands (a continuous 16th percentile can't exist when P(X=0) ≈ 35%+), and the fix flagged here — a discrete predictive distribution end to end — is now built. All 13 MLB stats use `count:poisson` XGBoost means + per-stat NB2 dispersion (`xgb_nbinom_{stat}_v2`), `predicted_std` is the distribution's true std, `modeling/edges.py`'s mean<5 Poisson stopgap is gone (replaced by the shared moment reconstruction in `modeling/distributions.py`, which now respects overdispersion on stats like total_bases/pitcher_strikeouts instead of assuming variance = mean), and `modeling/calibration.py`/`backtest.py` evaluate MLB stats via randomized PIT + discrete-percentile coverage. See §8's MLB calibration paragraph for the honest caveats: `backtest_runs.coverage_16/84` for MLB now overshoot nominal *by construction* (read them against the printed model-expected coverage), and the `modeling/eval_discrete.py` acceptance gate ran on synthesized lines because no settled prop lines exist yet. NBA stats are untouched (Gaussian + quantile + conformal, `xgboost_{stat}_v1`).
- **Assists calibration improved but not fully fixed** (see §8) — jittering calibration residuals before taking the quantile (to break the zero-inflated tie at residual=0) took coverage_16 from 32.6% to 8.4%, clearing the miscalibration warning but overshooting nominal (16%) in the other direction. A non-uniform jitter matched to the actual count distribution, or better features, is the next step if this matters once live betting starts.
- **Many predictions share an identical `predicted_mean`** across different players — e.g., 27 different players had the exact same predicted points value in one check. Very likely XGBoost routing players who lack rolling-average history (early in a season, or anyone without 5-10 prior games loaded yet) to the same leaf nodes, producing identical output. Not investigated further; worth a look once more of the season is backfilled and this either resolves itself or clearly doesn't.
- **XGBoost isn't seeded** (no `random_state` set anywhere) — exact MAE/calibration numbers will vary slightly between identical runs. Expected noise from the library's internal stochasticity, not a bug, but don't be alarmed if two `modeling.calibration` runs back-to-back don't match exactly.
- **Recommendations never scored — RESOLVED (2026-07-16)**: the system used to recommend parlays and flag edges without ever recording whether they'd have won. `modeling/settle.py` + `recommendation_outcomes` (migration `004_recommendation_outcomes.sql`) now settle both against actual results once their games finish — idempotent, FT-only, paper stake of 1 unit at the odds frozen when each was recommended. `GET /bet-performance` and the dashboard's "Betting record (paper)" section (Model-performance page) surface W-L-P and ROI. Migration applied to the live DB 2026-07-16 and the endpoint is live (returns `[]` until the first slate settles); wiring `settle` into the daily chain is deferred until after the 2026-07-17 parlay review (see §14.1).

**Dashboard / API**
- **No edge/parlay UI in the Next.js dashboard** — `web/` only has team/player browsing and predictions-vs-actuals (Phase 7 deliberately skipped this since `edges`/`parlay_recommendations` were empty at build time). Worth adding once real data exists to show.
- **No auth — RESOLVED (2026-07-15)**: the API now takes an `X-API-Key` header checked by a global FastAPI dependency (`api/auth.py`), gated by `AUTH_ENABLED` (unset/false = old open behavior, one env flip reverts) with keys in `PLAYSTAT_API_KEYS` (`name:key` pairs per consumer). The dashboard has a single-user login (`/login`) with an scrypt-hashed password and an HMAC-signed 7-day session cookie, enforced by `web/proxy.ts` and gated the same way: unset `SESSION_SECRET` disables it. See §10's Auth subsection and §7.1 for the Budgerr key options.
- **No backtest-trend chart** — only a handful of `backtest_runs` rows exist so far; a chart showing MAE/calibration over time is worth building once a couple weeks of daily runs have accumulated enough points to show a real trend.

**Budgerr integration**
- **Budgerr-side work hasn't started** — Playstat exposes `/edges`, `/parlay-recommendations`, and `/box-scores` (for auto-settlement), all verified working, but nothing in the Budgerr repo calls them yet. That's Budgerr's own build-order item 10, picked up whenever ready in that repo.

**Models lack per-game resolution — FUNDAMENTAL FINDING (2026-07-18), betting productization PAUSED**
- The whole "trust the numbers first" arc paid off by catching this *before real money*: **none of the models — player-prop or team-market — can predict individual games well enough to beat these markets.** They are roughly *calibrated* (get the average right) but have almost no *resolution* (can't tell games/players apart), and betting lives entirely on resolution.
- Evidence (all consistent): (1) player-prop `predicted_mean` tracks each player's own season rate with a regression slope of only **0.18–0.45** across the 13 stats (the model shrinks everyone toward the league average — see §8); (2) the first settled paper slate came in **~break-even (+0.8% ROI over 856 bets)** — no clear edge (see §8); (3) the F5 team model, built as the pivot to *escape* the player-shrinkage problem, has the **same failure, worse**: on a 1,256-game holdout `corr(predicted, actual)=0.081`, **R²=0.007** — it explains under 1% of game-to-game variance, `std(predicted)=0.36` vs `std(actual)=3.31` (it predicts ~5 runs for nearly every game); its `predicted_mean` tracks the book's F5 line with slope **0.185**. The large "edges" it flags (up to 23% at extreme lines like 7.0) are **shrinkage artifacts** — the model refusing to predict a high total for a slugfest the book prices at 7 — i.e. adverse selection, not signal.
- **It is a features/data problem, not a model-capacity problem** (verified 2026-07-18): making the F5 XGBoost *stronger* (40 trees/d2 → 300/d5 → 600/d7) made R² **worse** (0.0066 → 0.0048 → 0.0031) — a bigger model just fits noise. The current features (team rolling F5 scored/allowed + starter form; and the analogous player rolling features) carry almost no individual-game signal. Improving resolution therefore requires **genuinely new predictive data** (park factors, weather, umpire, lineups, deep pitcher/bullpen stats), not tuning — a real research bet against an efficient market, which may simply not be recoverable.
- **Decision (user, 2026-07-18): invest in model resolution first.** Do NOT deploy real-money betting or build the betting productization (dashboard edge/parlay surfaces, API contract, chain-swap) on top of the current low-resolution models — that would be infrastructure to display fake edges. **Acceptance gate before anything productizes for betting: a model must demonstrate real per-game *resolution* — `corr`/R² of predicted-vs-actual well above zero and `predicted_mean` tracking the market line with slope → 1 — not merely good calibration/Brier.** The resolution diagnostics used here (regress actual on `predicted_mean`; compare `std(pred)` vs `std(actual)`; slope of `predicted_mean` on the book line) are the permanent gate.
- **State of the team-market build**: Phases 0–3 of the NRFI+F5 pivot are *built, tested (143 pytest cases), and committed* but **DORMANT** — the code (`modeling/f5.py`, `modeling/team_edges.py`, `optimizer/team_parlay.py`, team-aware `modeling/settle.py`, migration `006_team_markets_f5.sql` applied, `runs_f5` backfilled 3 seasons) is correct plumbing that is *not* in the daily chain and *not* productized. It stays as the substrate to test any resolution improvement against, not as a live betting path. The daily `com.playstat.mlb` chain still runs the player pipeline unchanged (see the OOM caveat below).
- **Chain caveat**: the player-prop `optimizer.parlay` step OOM-died on 2026-07-18 (SIGKILL) — 1,060 edges>3% → C(1060,3)≈198M combinations, and a 100%-full disk left no swap. The heartbeat correctly alerted; the sentinel was hand-written to stop the catch-up re-run storm. It will recur nightly until the step is capped (e.g. top-N legs by edge before `find_combinations`) or removed. A partial mitigation is already committed (`MAX_CANDIDATE_LEGS = 200` in `optimizer/parlay.py`, bounding the search to `C(200,3)` ≈ 1.3M). **The real fix is designed in §15**: the low-risk parlay builder *replaces* this step outright (capped candidate pool + a 0.55 per-leg probability floor), so the runaway step goes away rather than being patched. Not yet built.

---

## 12. Session Notes (for whoever/whatever picks this up next)

This project's been built over a long-running Claude Code session that eventually hit the practical context-window limit. A few notes for next time, since that'll happen again on a project this size:

- **The context window is a model-level limit, not an interface one** — switching between Claude Code surfaces (terminal vs. a GUI wrapper) doesn't change how much it can hold. What actually helps is not relying on conversation history for anything durable.
- **§11 above exists for exactly this reason** — a fresh session should read "Known Issues & Follow-ups" first, rather than needing this session's full history to know where things stand.
- **Consider adding a `CLAUDE.md`** (doesn't exist yet) — Claude Code loads it automatically at the start of every session, which is a cheaper way to carry forward conventions/instructions than re-explaining them in chat each time. Keep it focused on *how to work in this repo*; leave the architecture/status narrative in this README.
- **`/compact`** (manually compact a long conversation) and **`/clear`** (reset the conversation, keep the project files) are both available in the terminal CLI if a session is getting unwieldy — better than losing everything by starting over, if there's context worth keeping.
- **The general pattern**: externalize durable state into files — this README in particular — rather than conversation memory. That's the actual fix for "long project, limited context," and it works the same regardless of which Claude Code surface is in use.

---

## 13. Roadmap — Multi-Sport Expansion & New Features

### 13.1 Multi-sport: MLB first, then NFL

**Why MLB first**: it's in season *right now* (mid-July), while `prop_lines`/`edges`/`parlay_recommendations` sit empty waiting on the NBA's ~October start (§11). Adding MLB isn't just an expansion — it unblocks the entire live odds → edges → parlay → Budgerr pre-fill pipeline with real data months early. NFL slots in second (preseason starts August), and the NBA rejoins a battle-tested pipeline in October.

**What it takes:**

- **Ingestion**: API-Basketball is one of the API-Sports family; API-Baseball (`v1.baseball.api-sports.io`) and API-American-Football share the same auth header and request/response shape, so `ingestion/api_client.py` mostly needs its base URL parameterized per sport. The odds client already takes a `leagueID` (`ingestion/odds_client.py`), so it's effectively multi-sport already. Check free-tier *season coverage* per sport before committing — already hit that wall once with NBA 2025-26 (§11).
- **Schema — done (2026-07-13)**: `db/migrations/001_multi_sport.sql`, applied to the live DB. A `sport` column on `teams`/`players`/`games`, and `player_game_stats` + `rolling_player_features` moved from hardcoded NBA columns (points/rebounds/assists, `pts_avg_5`…) to long format keyed by `stat_type`/`feature` name — no DDL needed per new sport. Downstream tables (`prop_lines`, `model_predictions`, `edges`, `backtest_runs`) were already `stat_type`-keyed and are unchanged. All ingestion/modeling/API code was updated in the same pass; verified post-migration: long row counts exactly match the legacy tables' non-null cells, `modeling.calibration` reproduces the §8 numbers (16.1%/18.0%/8.4% coverage_16), and `/box-scores`, `/players/{id}/stats`, `/players/{id}/predictions`, `/model-performance` return content-identical responses to pre-migration captures (Budgerr's auto-settlement contract is intact). The old wide tables were kept as `player_game_stats_wide_legacy`/`rolling_player_features_wide_legacy` — drop them once the daily job has run clean for a while. `/teams` and `/players` now also return a `sport` field (additive, backward-compatible).
- **ID collision — handled in the same migration pass**: `teams`/`players`/`games` PKs are API-Sports numeric IDs used directly, and each sport's API has its own *overlapping* ID space. New sports get a deterministic per-sport ID offset applied at ingestion (nba +0, mlb +100,000,000, nfl +200,000,000 — `SPORTS` in `ingestion/config.py`) so existing NBA rows and every FK stay untouched.
- **MLB ingestion — built and backfilled (2026-07-13)**: provider question resolved empirically — API-Sports' baseball API has **no player box-score endpoint** (`/games/statistics/players` doesn't exist there), so MLB runs on **MLB's official StatsAPI** (`statsapi.mlb.com` — free, no key, no meaningful quota) via `ingestion/mlb_backfill.py`, a parallel path to the API-Sports-based `backfill.py`. Players are derived from box scores (not rosters) so traded/called-up players can't break the stats FK; StatsAPI's final state maps to `games.status='FT'` to keep the existing API/Budgerr convention; postponed games re-listed under the same gamePk resolve to their makeup date via the upsert. The full 2026 regular season to date (30 teams, ~2,430 games, ~1,444 final) is loaded. Stat vocabulary (chosen to line up with prop markets) — batters: `hits`, `total_bases`, `home_runs`, `rbis`, `runs`, `stolen_bases`, `batter_strikeouts`, `walks`, `at_bats` (exposure, the minutes analogue); pitchers: `pitcher_strikeouts`, `earned_runs`, `hits_allowed`, `walks_allowed`, `outs_recorded`. `/box-scores` now also returns a generic per-player `stats` map + `sport` field (additive; the top-level points/rebounds/assists NBA contract is unchanged) and takes an optional `?sport=` filter.
- **MLB odds mapping — done (2026-07-13)**: `ingestion/odds_ingest.py` is sport-parameterized (`--sport mlb`) with per-sport `STAT_MAPS` translating SportsGameOdds statIDs to our stat_types — all 13 MLB stat types map, verified against the live feed, including the non-obvious one (SGO normalizes each sport's primary scoring stat to `"points"`; MLB market names confirm it's runs). `matching.py` is sport-filtered now, and game matching converts UTC start times to local dates (`utc_start_to_local_date` — subtracting 6h recovers the home-local date for any US game; the naive UTC date mismatches every night game, and a "date or day before" fallback is unsafe in MLB where series put the same matchup on consecutive days). First real pull: ~1,000 prop lines across 6 events, 5 unmatched player names out of ~1,000 lines, All-Star Game correctly unmatched (not a regular-season game). `modeling/edges.py` now skips one-sided lines (~8% of live MLB lines have only one side quoted; de-vigging needs both) instead of crashing. Doubleheaders remain inherently ambiguous by (teams, date) — both legs' lines attach to one of the two games; acceptable until it isn't.
- **MLB modeling — built (2026-07-13)**: `modeling/features.py` is sport-generic (per-sport `SPORT_CONFIG` windows; MLB gets 16 rolling features incl. 3-start pitcher windows and `ab_avg_5` as the playing-time exposure), `STAT_CONFIG` covers all 13 MLB prop stats (entries now carry their sport), and — the piece that was missing for *any* sport — `modeling/predict_upcoming.py` predicts games **before they're played**: `features.py --upcoming-days N` synthesizes as-of feature rows for scheduled games, and predict_upcoming trains on full history and writes `model_predictions` for them. Previously predictions only existed for already-played games (backtest-only), which is why `/edges` could never fire even with live lines. Caveat: `edges.py` converts mean/std to probabilities via a Gaussian, which is crude for 0.5-line markets (home runs, stolen bases) — treat those edges skeptically; a count-distribution upgrade is future work.
- **Daily MLB job — scheduled**: `com.playstat.mlb` (launchd, 8:30am, `~/Library/LaunchAgents/`, logs to `logs/mlb.log`) chains the full loop: box scores → linescores/starters → **CLV scoring** → features (+2d upcoming) → predictions → odds snapshot → first-inning model → edges → **MLB backtests** (`modeling.backtest --sport mlb`, so coverage/MAE trends accumulate in `backtest_runs` for all 13 MLB stats) → parlay (2x target, ≤3 legs, ≥3% edge).
- **Training data: 2024+2025+2026 seasons** — backfilled 2026-07-13 (~7,300 games, ~1M stat rows) after every model finding pointed at one-season data as the binding constraint. `modeling/features.py`'s writer was batched in the same pass (row-at-a-time upserts took ~35 min/season; the multi-season rebuild takes a couple of minutes).
- **NFL ingestion — built and test-verified (2026-07-14)**: provider question resolved empirically via a live probe — NFL runs on **nflverse-data's GitHub release CSVs** (`nflverse/nflverse-data`, static HTTPS, free, no key, no rate limit; CC-BY-4.0 licensed) instead of API-Sports, via `ingestion/nfl_backfill.py`. The schedule file (`schedules/games.csv`, seasons 1999–2026 in one file) and per-season player box scores (`stats_player/stats_player_week_<season>.csv` — nflverse retired the older `player_stats` release assets mid-2025, renaming `recent_team`→`team` and `interceptions`→`passing_interceptions`; all seasons are pulled from the new asset so there is one schema) already carry prop-market-shaped columns — verified Mahomes' 2024 Week 1 row (`passing_yards=291`, `passing_tds=1`) matches API-Sports exactly. API-Sports' american-football API (`SPORTS['nfl']` in `ingestion/config.py`) is kept only as an unverified fallback reference (100 req/day quota) and is not called by this module. ID scheme, since nflverse uses string IDs, not numeric ones: teams get `id_offset + canonical_index` over a hardcoded sorted list of the 32 current abbreviations (`CANONICAL_TEAMS` in the module — set-independent, so IDs can't shift with the seasons pulled; pre-2016 relocation aliases like OAK/SD/STL raise a clear error, acceptable since only 2023+ is needed); games get `id_offset + season*100000 + week*1000 + canonical_index(home_team)` — within one (season, week) each team hosts at most one game, so the home team is a unique key that, unlike a rank within the week's game set, can't be shifted by a cancellation or a cross-week postponement (nflverse's own numeric ID candidates — `espn`, `old_game_id` — are blank or inconsistently formatted for ~270 rows including the whole unplayed current season, so they were rejected); players use `id_offset + int(trailing digits of '00-0033873'-style player_id)`, and — like MLB — are derived from box-score rows rather than a roster pull so mid-season trades/call-ups can't break the `player_game_stats` FK. `season_type` is only ever `REG`/`POST` (verified 2023-2025) — there's no preseason to filter out, so all regular-season and playoff rows from both files are ingested; rows are filtered to offensive position groups (`QB/RB/WR/TE`) because the new nflverse files cover every rostered player and defenders/OL/kickers carry literal 0s in every offensive column (~60% of the file) that would otherwise fill `player_game_stats` with zero rows and pollute rolling-feature computation. Stat vocabulary: `passing_yards`, `passing_tds`, `interceptions_thrown` (renamed from nflverse's `interceptions` to avoid colliding with a future defensive-INT stat), `completions`, `pass_attempts`, `rushing_yards`, `rushing_tds`, `carries`, `receiving_yards`, `receiving_tds`, `receptions`, `targets`. **Fully backfilled 2026-07-15** (`--seasons 2023,2024,2025`): 32 teams, 855 games, 870 offensive players, 224,976 stat rows (74k/74.4k/76.6k per season), zero FK orphans, Mahomes 2024 Wk1 spot-check exact (291 yds / 1 TD / 1 INT), idempotent re-runs verified. Known perf debt: the writer upserts row-at-a-time (~15+ min for the full backfill) — batch it like `modeling/features.py`'s writer if it starts to matter; incremental weekly updates are small enough not to care.

### 13.3 First-inning market (MLB team-level)

Built 2026-07-13, per-user request ("will the first inning stay under 1.5 runs, studying both teams"):

- **Data**: `team_game_stats` (migration `002_team_game_markets.sql`) holds per-team `runs_inning_1` and `runs` from StatsAPI linescores (one hydrated schedule request covers the season; `ingestion/mlb_backfill.py --only linescores`). 2026 base rate: **71.5% of games stay under 1.5 total first-inning runs** — any model here is fighting that prior, not a coin flip.
- **Model**: `modeling/first_inning.py` (v2, `xgb_fi_v2`) — features are each team's rolling 15-game first-inning runs *scored* and *allowed*, plus **each side's starting pitcher's form**: ERA-style and WHIP-style rates + workload over their last 10 appearances (leakage-safe shift(1) for training; current form for upcoming games). Starters come from StatsAPI's `probablePitcher` hydrate, stored as `team_game_stats` stat `starter_player_id` for played *and* scheduled games (45/45 recent finals have it; upcoming games fill in as starters are announced). P(under 1.5) is a small XGBoost binary classifier — a Poisson conversion from predicted mean ran ~7 points low on holdout (first-inning runs are overdispersed vs Poisson). **Holdout honesty**: on one season of data (n=275) the model couldn't beat always-predicting-the-base-rate (Brier 0.1997 vs 0.1972) across three starter-feature encodings. After backfilling 2024+2025 (n=1,256 holdout), it reached parity — Brier 0.2051 vs 0.2053 baseline, mean predicted P(under) 73.6% vs 71.3% empirical — confirming data was the constraint. The probabilities are *calibrated, marginally better than the prior* — treat `prob_under` as a fair price estimate; the edge (if any) is thin, and every completed season adds training data.
- **Lines + serving**: `game_lines` ingests SGO's game-level "1st Inning Over/Under" (statID `points`, entity `all`, period `1i` — books quote 0.5, the NRFI line; our `predicted_mean` lets any line's probability be derived). `game_predictions` stores model output; `GET /game-predictions?date=&sport=` serves both side by side.
- **Modeling**: `STAT_CONFIG` in `modeling/train.py` is already a per-stat dict — extend it with MLB stats (hits, total bases, strikeouts, home runs, …). Caveats worth knowing up front:
  - MLB counts are even more zero-inflated than assists — a stress test for `_jittered_quantile` (§8); the non-uniform jitter flagged in §11 may become necessary rather than optional.
  - NFL: 17-game seasons mean 5/10-game rolling windows span most of a season and conformal calibration slices get thin — plan on multi-season training data and wider uncertainty.
  - Same-game correlation is much stronger in NFL (a QB's passing yards and his WR's receiving yards are tightly coupled) — the optimizer's same-game exclusion (§6) becomes essential rather than conservative.
- **Budgerr knock-on**: Budgerr's auto-settlement whitelist only resolves `points|rebounds|assists` — it needs the new stat types added (tracked in Budgerr's own README roadmap).

### 13.2 New features (sport-agnostic)

- **Line-movement / closing-line-value (CLV) tracking — built (2026-07-13)**: `modeling/clv.py` + migration `003_clv.sql`. Every edge on a finished game gets a `clv_records` row: de-vigged implied probability of our side at the closing line minus at the line when the edge was first flagged (`edges.created_at`, preserved across upserts). Positive average CLV = the market keeps moving toward our positions = the edges are real; negative = we're being picked off. `GET /clv-summary` aggregates by stat type (multi-snapshot edges only — a single line pull records CLV 0 with `n_snapshots=1` and is excluded). Runs daily in the `com.playstat.mlb` chain right after box scores finalize games; first real records land the morning after the first slate with flagged edges finishes (Sat 2026-07-18).
- **Database scaling (considered, deliberately not done)**: sharding — splitting rows across multiple database servers — does not fit this project and was explicitly ruled out (2026-07-14). The whole dataset is ~1M stat rows written by a once-daily batch job; a single Postgres handles that with no strain, and the core queries (edges: prop_lines → model_predictions → players → games; the parlay optimizer across games) join across tables in ways that cross-database splits would break. Per-sport separation already exists logically via the `sport` column + per-sport ID offsets. If per-sport data volume ever actually hurts (tens of millions of rows — many seasons × many sports away), the right-sized step is Postgres **native table partitioning** (`PARTITION BY LIST (sport)` on `player_game_stats` / `rolling_player_features`) on the same server — same SQL, per-sport physical sub-tables — not sharding.
- **Per-edge "why" panel**: surface the feature values driving each prediction (rolling averages, opponent rating, rest) via XGBoost feature contributions, so an edge can be sanity-checked before it's trusted.

---

## 14. Future Directions (architected 2026-07-15)

Where the project can go next, prioritized by an architecture pass after items 1–4 of the July backlog landed (discrete MLB distributions, NFL ingestion, edge/parlay/CLV dashboard, API auth). Each item is written to be pick-up-able by a fresh session without extra context. Tiers are ordered; within a tier, order is a suggestion.

### 14.1 Trust the numbers first (highest value, ready now)

- **Real-line evaluation gate**: `modeling/eval_discrete.py`'s old-vs-new comparison ran entirely on synthesized lines because no settled prop lines existed yet. Once a week or two of settled `prop_lines` accumulate (starting ~2026-07-18), re-run it against *real* lines and real outcomes, and add that join path to the script permanently. This is the single most informative unbuilt thing: it converts every downstream idea from "probably" to "measured".
- **Bet-outcome tracking (paper-trading ledger) — BUILT (2026-07-16)**: the system recommended parlays but never recorded whether *it would have won*. `recommendation_outcomes` (migration `004_recommendation_outcomes.sql`) is a settlement ledger — one row per recommended parlay (`bet_type='parlay'`) and one row per flagged edge above the optimizer's min-edge (`bet_type='edge'`, matching `optimizer/parlay.py`'s `DEFAULT_MIN_EDGE` of 3%), written once each. `modeling/settle.py` mirrors `modeling/clv.py`'s shape (`if __name__ == "__main__": settle(db.get_engine())`, runnable as `python -m modeling.settle`): it settles idempotently (a `NOT EXISTS` guard per bet, like CLV's), only against games that are `status='FT'` with the leg's `player_game_stats.value` present — a parlay with any leg not yet ready is skipped whole and retried the next run. Per-leg odds are the ones frozen in the recommendation's own JSONB at rec time (parlay legs) or the `prop_lines` snapshot in effect at `edges.created_at` (edge bets) — never the closing line, since this is a paper-trading P&L ledger, not a CLV measure. A pushed leg inside a parlay is dropped and the combined decimal odds are recomputed over the remaining hit legs (standard sportsbook push handling); an all-push parlay is itself a push. The scoring math (`settle_leg`, `parlay_result`, `single_pnl`) is factored into pure, DB-free functions, unit-tested in `tests/test_settle.py` (21 cases: over/under × hit/miss/push, all-hit win, one-miss loss, pushed-leg-dropped-and-recomputed, all-push, stake scaling, and `american_to_decimal`/`odds_to_probability` round-trips). `GET /bet-performance` (`api/main.py`, `BetPerformanceOut` in `api/schemas.py`) aggregates W-L-P/staked/P&L/ROI per `bet_type` plus an `'all'` row, behind the existing global API-key dependency. The dashboard's Model-performance page (`web/app/clv/page.tsx`) got a new "Betting record (paper)" section above the CLV table, with a correct empty state until the first slate settles. Verified end-to-end against a throwaway `playstat_ledger_test` DB (not the live `playstat` DB): synthetic parlays/edges covering all-hit, one-miss, pushed-leg, and not-yet-final cases settled to the expected result/pnl, a second run inserted zero new rows (idempotent), and a spare-port API returned correct `/bet-performance` JSON. **Migration applied to live DB 2026-07-16** (architect); `python -m modeling.settle` was then run once against the live DB and correctly settled nothing (30 recommended parlays + 75 edges>3% all sit on not-yet-`FT` games — "30 not yet ready"), confirming it no-ops safely until games finish. **Settle wiring — DONE 2026-07-17 (architect)**: `python -m modeling.settle` now runs right after `modeling.clv` in [`scripts/daily_chain.sh`](scripts/daily_chain.sh) (see the "Daily-job observability" bullet below). Landed after the Friday 2026-07-17 9:30am first-parlay review fired, so the chain feeding that review was unchanged; the first settlements land ~7/18 as the 7/17 slate finishes.
- **Test suite + CI — BUILT (2026-07-16)**: the pure-math core now has a 98-case pytest suite plus a GitHub Actions workflow, on top of the 21 pre-existing `tests/test_settle.py` cases from the paper-trading ledger. New coverage: `tests/test_distributions.py` (23 cases — `modeling/distributions.py`'s moment reconstruction incl. the Poisson/NB boundary at `std² == mean`, `prob_over`/`prob_over_discrete`/`prob_over_gaussian` checked against `scipy.stats.poisson`/`nbinom`/`norm` survival functions, half-integer vs. integer line handling, monotonicity in `mean`, and mechanical properties of `cdf_array`/`ppf_array`/`randomized_pit`, including an approximate-uniformity Monte Carlo check); `tests/test_odds.py` (19 cases — `odds_to_probability`/`devig` symmetry, monotonicity, and overround removal); `tests/test_parlay.py` (11 cases — `american_to_decimal` plus `find_combinations` same-game exclusion, tolerance filtering, `joint_prob`/`combined_odds` products, descending sort, and min/max leg bounds, built on synthetic leg dicts); `tests/test_ids.py` (11 cases — `ingestion/config.py`'s per-sport `id_offset` disjointness and `ingestion/nfl_backfill.py`'s canonical team/game/player ID scheme: determinism, set-independence — an ID surviving cancellations/partial pulls unchanged — within-week uniqueness, and the pre-2016 relocation-alias (`OAK`/`SD`/`STL`) error); `tests/test_train_helpers.py` (13 cases — `_jittered_quantile` determinism and its zero-inflation fix reproduced on a synthetic 70%-atom-at-0 residual sample, `stat_family`/`model_version`/`stats_for_sport`, and `predicted_std_from_quantiles` incl. the quantile-crossing clamp). A root `conftest.py` sets dummy `DATABASE_URL`/`API_BASKETBALL_KEY` defaults before any test module imports `ingestion.config` transitively, so the whole suite runs with no `.env` and no real credentials — verified by running under a fully stripped environment (`env -i`) from outside the repo. `.github/workflows/ci.yml` runs `pytest -q` on Ubuntu/Python 3.11 for every push/PR to `main`, with the same dummy env vars set at the job level as belt-and-suspenders. **The workflow file is written but NOT yet committed/active**: Claude Code's push credential lacks GitHub's `workflow` OAuth scope, so `.github/workflows/*` can't be pushed from these sessions — the file sits in the working tree; activate CI by committing it yourself, or run `gh auth refresh -h github.com -s workflow` to grant the scope and then push it. The suite itself is landed and green locally. Deliberate v1 scope: pure-math only — no DB/integration tests yet (`modeling/train.py`'s `fit_discrete`/`fit_gaussian`/`load_dataset` are untested since they need a live DB and/or actually fit an XGBoost model).
- **Daily-job observability — APPLIED 2026-07-17 (architect)**: the chain body moved out of the `com.playstat.mlb.plist` `bash -c` string into a real, version-controlled script, **[`scripts/daily_chain.sh`](scripts/daily_chain.sh)**, which the plist now `ProgramArguments`-invokes. The script (a) runs the full pipeline with `python -m modeling.settle` inserted right after `modeling.clv`, (b) pings `HEALTHCHECK_PING_URL` only as the last step on full success and pushes `NTFY_TOPIC` on any failure, and (c) **self-heals a missed run**: launchd re-fires a missed `StartCalendarInterval` on *wake* but NOT after a *boot* past the trigger time — which is exactly what silently skipped the 2026-07-17 8:30 run (the Mac booted 08:47; `runs=0`, zero edges/parlays for the day until the architect kickstarted it manually). The plist now also sets `RunAtLoad` + `StartInterval 1800`, and the script guards with a per-day sentinel (`logs/.last_success`) and an 08:30–12:00 window so those extra triggers no-op unless the chain still owes a run. **A bug in the originally-researched design (above) was caught and fixed during implementation: launchd does NOT read `.env`** — it hands the job only a minimal `PATH`/`SSH_AUTH_SOCK` — so a bare `$HEALTHCHECK_PING_URL` in the plist string would have expanded to empty, curling nothing, and healthchecks.io would have cried "missed" every morning. The script sources `.env` itself before any curl. Values stay in the **gitignored `.env`** as `HEALTHCHECK_PING_URL`/`NTFY_TOPIC` (never committed). Both alert paths were smoke-tested live against the real endpoints (a real "chain FAILED" push landed on the user's iPhone); the plist swap (`launchctl bootout`/`bootstrap`) was verified to load and correctly no-op outside the window. Original research retained for provenance: in the plist `bash -c` string, append `&& curl -fsS -m 10 --retry 3 https://hc-ping.com/<uuid>` and wrap in `{ ...; } || curl -d "MLB daily chain failed" ntfy.sh/<topic>` — superseded by the script above precisely because of the `.env` gotcha and because a script ports cleanly to the Budgerr systemd migration.
- **outs_recorded dispersion refinement**: the one honest regression from the discrete cutover (log-loss 0.1786→0.1827) — fit mean-binned NB dispersion (starters vs relievers have different variance regimes) instead of one global r. Small, contained, measurable.

### 14.2 Bet better (after the numbers are trusted)

- **CLV-gated edge filtering**: once `clv_records` accumulate, stop treating all stat types equally — only feed the parlay optimizer stat types whose average CLV is positive (the market confirms our reads there). One WHERE clause + a README note; turns CLV from a report into a control loop.
- **Kelly-criterion stake sizing**: edges currently say *what* to bet, never *how much*. Researched policy (§14.6): **¼-Kelly per parlay treated as a single bet** (`f* = (bp − q)/b` on the parlay's joint probability vs its combined odds; ¼ because parlay probability estimates compound per-leg estimation error), plus a **hard same-night total-exposure cap (~20% of bankroll)** with proportional scale-down when individual stakes sum past it — same-night bets are not independent bankroll draws. Surface both raw and after-cap fractions on the dashboard. Requires calibrated probabilities — hence tier 1 first.
- **Line shopping / multi-book support**: `prop_lines` stores one consensus line per pull, but **every SGO response we already receive carries a `byBookmaker` breakdown that `ingestion/odds_ingest.py` (~L84) currently throws away** — live-verified 2026-07-15 (§14.6): 9 books on our free tier (fanduel/draftkings/bovada/espnbet/caesars/...), spreads of 20+ American-odds points on the same prop observed. Additive `book` column on `prop_lines`/`game_lines`, edge computation takes the best available price per side. Zero extra quota cost (SGO bills per event, not per book/market), works today. The cheapest real ROI improvement available, no modeling required.
- **Same-game correlation modeling**: the optimizer excludes same-game combos entirely (§6) — correct but conservative, and NFL makes it expensive (QB passing yards ↔ WR receiving yards ↔ receptions are the marquee correlated parlays books love to sell; in MLB, same-team hits↔runs↔total bases correlate through lineup cycling). Researched 2026-07-15 (§14.6): **v1 = empirical joint frequencies** from our own box-score history, restricted to a short list of high-value same-team pairs — no new modeling family, auditable co-occurrence tables; known bias: thin-sample noise without ~a season of shared history. **v2 = Gaussian copula over the discrete marginals** — but copulas on discrete distributions are non-unique (ties break the probability integral transform); the standard fix is jittering/continuizing the marginals, machinery this repo already half-has. Don't front-load v2 — opus-grade when it comes.
- **Edge alerting**: ntfy.sh push when an edge above a threshold appears on a fresh odds pull. **Quota reality check (live-verified 2026-07-15, §14.6): the free SGO tier is 2,500 entities/month and we use ~450; hourly game-day pulls ≈5,400/month — blows the free tier in under a week and needs the $99/mo Rookie plan.** So: either stay 1×/day (alerting adds little) or make the paid-plan decision first — **user's call**, don't start without asking.

### 14.3 More markets, more sports

- **NFL modeling (data is loaded, models aren't)**: 3 seasons of NFL stats are in the DB (§13.1) but `SPORT_CONFIG`/`STAT_CONFIG` have no NFL entries. Needs: rolling features tuned to 17-game seasons (3/5-game windows + multi-season training, per §13.1's caveats), NFL odds mapping in `odds_ingest.py`'s `STAT_MAPS`, and yardage stats need a *continuous* family (normal/gamma — yards aren't counts; receptions/TDs stay discrete). SGO statIDs **live-confirmed 2026-07-15** (§14.6): `passing_yards`, `passing_touchdowns`, `passing_interceptions`, `rushing_yards`, `receiving_yards`, `touchdowns`, `firstTouchdown`/`lastTouchdown`; **`receiving_receptions` is in SGO's docs but wasn't live yet in mid-July** — re-check in early August when preseason props open before wiring it in. Preseason starts August — this is the natural next big build.
- **NBA October readiness** (§11 carry-overs): assists calibration follow-up (non-uniform jitter or the discrete-distribution treatment now proven on MLB — likely the better answer), the identical-predicted-mean investigation (players lacking rolling history routing to the same XGBoost leaves), and the unbuilt §4 features (injury reports, real pace/def_rating). Deadline-driven: needs to be done before tip-off ~October.
- **First-inning model, applied**: `game_predictions` + NRFI lines exist and are served, but game-level markets never enter the parlay optimizer or the edges table. Either wire `game_lines` into a game-level edge computation (small) or deliberately park it (document which).
- **New sports (NHL/soccer)**: the multi-sport schema + ID-offset pattern makes each new sport mostly an ingestion problem now. Only worth it when there's actual betting interest — the pipeline generalizes, attention doesn't.

### 14.4 Platform (when it leaves the laptop)

- **Deployment** (auth prerequisite ✅ done): researched decision table (§14.6) confirms **Tailscale Serve** as the right first step — free (Personal plan), automatic HTTPS inside the tailnet, zero public attack surface, zero migration (points at the existing localhost ports); the only cost is the phone needing the Tailscale app. Tailscale **Funnel** (public URL, same zero-migration) or a VPS ($5–10/mo Hetzner/DO + Postgres/systemd/caddy migration) only become right if the laptop must be off while serving or someone outside the tailnet needs access. **Decision still needs the user** (exposure tradeoffs), but the default is now researched, not guessed.
- **Deploy-prep artifacts — BUILT (2026-07-17)**, in response to the Budgerr session's combined-Compose coordination (Budgerr §15.2: containerize both APIs into one stack on a single host, systemd timers replacing launchd, Tailscale for phone access). `Dockerfile` at the repo root: `python:3.11-slim` (matches the 3.11.9 venv + CI), `libgomp1` (**real gotcha**: xgboost links OpenMP at runtime and slim omits it — without it the image builds fine and then dies at `import xgboost`), `curl` for the healthcheck; copies `api/ ingestion/ modeling/ optimizer/ db/` because **one image serves two uses** — the always-on API (default CMD `uvicorn api.main:app --host 0.0.0.0 --port 8000`) and the daily batch chain run as different commands off the same image. Build-verified, not theoretical: image builds (591 MB linux/amd64), and inside it `xgboost 3.2.0`/scipy/numpy/psycopg2 and `api.main` all import. `.dockerignore` keeps `.env`/`web/`/`.venv`/`graphify-out` out. `GET /health` (`api/main.py`) is an unauthenticated liveness+readiness probe returning `{"status":"ok","database":"ok"}` (503 if Postgres is unreachable — it checks the DB, since every endpoint reads it), exempted via `api/auth.py`'s `PUBLIC_PATHS` so a compose healthcheck needs no API key; verified the exemption is exactly one path (every other endpoint still 401s without a key and on a bad key). **arm64 is expected-good but unverified** — the base image and every wheel publish for it, but only linux/amd64 was built here; do a `--platform linux/arm64` build early if the target is a Pi 5. **Hardware caveat for that choice**: the 8:30am chain *retrains XGBoost every morning* (13 MLB stats, NFL later) — minutes on an M-class Mac, materially slower on a Pi 5; time it on the target before committing. Migration reality: Postgres 14.22, DB `playstat` is **701 MB** (3 MLB + 3 NFL seasons + NBA) — a real pg_dump/restore onto a persistent volume, not a service that starts empty.
- **Secrets hygiene at deploy time**: `.env`/`web/.env.local` are fine on a personal laptop; a deployment needs real secret management (even just systemd credentials / an env file with tight perms) and HTTPS before the API key crosses a network.
- **Batched ingestion writers**: known debt (§13.1) — `nfl_backfill.py` (and `mlb_backfill.py`) upsert row-at-a-time; the full NFL backfill took >10 min. Copy `modeling/features.py`'s batching. Matters the day backfills become routine (new sports, re-ingests).
- **Postgres partitioning**: already scoped in §13.2 — only at tens of millions of rows.

### 14.5 Dashboard polish (steady-state improvements)

- **Distribution visualization per edge** — *built* (2026-07-17). Every MLB prediction is a full PMF; the edges page now shows it. New read-only `GET /edge-distributions` endpoint (`api/main.py`, `EdgeDistributionOut` in `api/schemas.py`) returns, for every current positive edge, `{player_id, game_id, stat_type, side, family, line_value, predicted_mean, prob_over, prob_under, pmf}` where `pmf` is `[{k, prob}, …]` (null for gaussian/NBA). The PMF is reconstructed from the stored `(predicted_mean, predicted_std)` via `modeling/distributions.pmf_list` (a new pure helper: `[(k, P(X=k)), …]` for `k=0..min(discrete_ppf(cover), k_cap)`), so it matches edge computation exactly — the bars' over-mass equals the row's `prob_over` (`= prob_over_discrete`) to floating-point tolerance. Same `prop_lines` DISTINCT-ON latest-pull join and same `model_version` match (`modeling.train.model_version`) as `/edges`, additive only — `/edges` and the rest of the Budgerr contract are untouched. Frontend: the edges table (`web/app/edges/EdgesExplorer.tsx`) gains a per-row expansion (accessible toggle button, `aria-expanded`/`aria-controls`) rendering a self-contained SVG bar chart (no chart library): one bar per k, the line marked with a dashed vertical rule at `floor(line)+1`, the edge's winning side in signal-green and the other side muted, plus `P(over)`/`P(under)`/mean/line labels in Geist Mono. Gaussian/empty rows show a "distribution shown for MLB (discrete) stats" note. Tests: `tests/test_pmf.py` (21 pure-math cases: `[0,1]` bounds, covered-mass sum, scipy Poisson/NB oracle match, `k_cap`, degenerate small-mean, and `pmf_list` over-mass vs `prob_over_discrete`). **Note for the line-shopping branch**: `/edge-distributions` reads `prop_lines` for `line_value` the same way `/edges` does; when the `book` column lands and consumers pin `book='consensus'`, this query needs the same consensus pin.
- **Per-edge "why" panel** (carried from §13.2): XGBoost feature contributions per prediction.
- **Backtest-trend chart** (§11): `backtest_runs` has been accumulating daily since 2026-07-13; once a few weeks exist, chart MAE/calibration over time on the model-performance page.
- **Player pages for MLB/NFL**: player detail views are NBA-shaped; the generic `stats` map from `/box-scores` makes sport-aware game logs straightforward.
- **PWA / phone layout pass**: game-day usage is a phone at a bar, not a laptop. The tables already scroll horizontally; a `manifest.json` + viewport audit gets 80% of the way.

### 14.6 Research appendix (2026-07-15)

Findings from a targeted research pass (live SGO API probes with our key + web research; full report was session-scoped, durable facts recorded here):

- **SGO plan/quota, live-verified**: we're on the free "amateur" tier — **2,500 entities/month, 10 req/min** (why `odds_client.py` paces at 6.5s), currently using ~450/month at 1×/day pulls. Check anytime via `GET /v2/account/usage/` (undocumented but works with our key). Billing is **per event returned, not per market or bookmaker** — so richer parsing of responses we already get is free; more *pulls* is what costs. Hourly game-day pulls ≈ 5,400 entities/month → requires Rookie ($99/mo, 100k/mo). Paid-tier bookmaker counts (beyond our 9) couldn't be verified without upgrading.
- **SGO per-book odds**: every `odds` entry in every current response carries `byBookmaker` (9 books on free tier: fanduel, draftkings, bovada, espnbet, caesars, …) alongside the consensus `bookOdds`/`bookOverUnder` that `odds_ingest.py` reads today. Responses carry the upsell notice "missing 2362 bookmaker odds" — that's the paid-tier gate, not an error.
- **NFL statIDs (live scan of 20 events)**: confirmed live — `passing_yards`, `passing_touchdowns`, `passing_interceptions`, `rushing_yards`, `receiving_yards`, `touchdowns`, `firstTouchdown`, `lastTouchdown`, game-level `points`. In docs but not yet live mid-July: `receiving_receptions`, `passing_completions`, `passing_attempts`, `rushing_attempts`, `receiving_yardsAfterCatch`. Re-verify live in preseason (early August) before building `STAT_MAPS["nfl"]`.
- **Monitoring**: healthchecks.io free tier = 20 checks, schedule-aware missed-ping alerts, bare `curl` suffices (email/webhook alerts free; SMS no longer free as of mid-2026). ntfy.sh free = 250 msgs/day, no account, `curl -d "msg" ntfy.sh/<topic>`; Android app mature, iOS app functional but rough — fine for this volume. Self-hosted uptime-kuma rejected: no cron-schedule awareness, adds a service to maintain.
- **Correlation literature**: copulas over discrete marginals are non-unique (ties break the probability integral transform) — jitter/continuize marginals (cf. `_jittered_quantile`) or use rectangle probabilities on the joint CDF. Shared-latent-factor count models (bivariate Poisson, Dixon–Coles-style) are the principled alternative but a bigger lift. Empirical joint frequencies are the auditable v1 (§14.2).
- **Kelly for parlays**: no canonical formula; the repeated practitioner pattern is ¼-Kelly per parlay-as-one-bet + a same-night total-exposure cap (20–25% of bankroll) with proportional scale-down (§14.2).
- **Deployment table**: Tailscale Serve (tailnet-only, free, auto-HTTPS, zero migration, phone needs the app) vs Funnel (public URL, free, adds public surface) vs VPS ($5–10/mo + Postgres/systemd/caddy migration; only option that serves while the laptop is off) (§14.4).

### Status snapshot (2026-07-15)

Built and live: 3 MLB seasons + 3 NFL seasons ingested; 13 discrete-distribution MLB prop models (v2) + first-inning model; daily 8:30am chain; live odds → 116 edges → 10 parlay recommendations for the 7/17 slate; CLV pipeline armed (first records ~7/18); dashboard with edges/parlays/model-performance pages; API-key auth + dashboard login. First real-money checkpoint: the scheduled Friday 7/17 9:30am parlay review.

---

## 15. Low-Risk Parlay Builder — DESIGNED 2026-07-18, NOT YET BUILT

Full design doc: [`docs/superpowers/specs/2026-07-18-low-risk-parlay-builder-design.md`](docs/superpowers/specs/2026-07-18-low-risk-parlay-builder-design.md). This section is self-contained — a fresh session can go straight from here to `writing-plans` → build without re-brainstorming. **Every decision below is user-confirmed (2026-07-18); do not relitigate them.**

### 15.1 Goal (user reframe, 2026-07-18)

Build a low-risk parlay **BUILDER, not a market-beating model**. Construct a near-double / double (~2x) payout from multiple low-risk legs, mixing MLB **player props** and **team props** (NRFI = 1st-inning runs, F5 = first-5-innings runs). The user explicitly is **NOT** asking to beat the market and accepts it may not be +EV. The job is *the safest construction of a ~2x parlay, surfaced honestly.*

This is the deliberate answer to §11's fundamental finding — the models lack per-game resolution and can't beat these markets, so **the builder does not depend on them**. It is an honest constructor + paper-trading sandbox, not a betting product.

### 15.2 The honesty core (the thing that makes this design correct)

- In a devigged (efficient) market a parlay's true joint probability is roughly **1/payout** no matter how it's built — a 2x parlay is **~48–50% to hit**, a coin flip.
- Every extra leg adds another **vig bite**, lowering real probability. At a fixed payout, **fewer legs is strictly safer**.
- "Low risk" and "2x" pull against each other. The builder's whole job is to make that tradeoff **visible** and pick the least-bad point. For genuinely low risk (75%+) the honest target is **~1.3–1.5x**, not 2.0x.
- Rank on **MARKET-implied (devigged) probability**, never model probability — §11: the models are roughly calibrated but have almost no resolution and **overstate the safety of heavy-favorite legs**, exactly the legs a 2x builder leans on. The book's devigged price is the best-calibrated probability we have.

### 15.3 Decisions (user-confirmed)

| # | Decision | Choice |
|---|---|---|
| 1 | Interaction | **Two-axis.** User pins either target payout **or** a minimum joint-probability floor; the builder optimizes/bounds the other. Both always surfaced. |
| 2 | Build order | **Engine + API first, dashboard second.** Two reviewable stages. |
| 3 | Same-game legs | **Across-game only in v1** (independent → joint = product). Same-game deferred (§15.9). |
| 4 | Model's role | **Non-authoritative display context only.** Rank/filter purely on market prob; show `model_prob` labelled "not used for ranking." |
| 5 | Nightly step | **Builder replaces the OOM-dying `optimizer.parlay` step**; daily chain writes its picks, existing `settle.py` scores them. |
| 6 | Leg menu | **All markets, favorite-side only, per-leg devigged-prob floor ≥ 0.55** (tunable). Market price decides what's safe — no hardcoded stat blacklist. |
| 7 | Leg count | **2–4 legs, prefers fewest.** Joint-prob ranking surfaces 2-leg constructions first. |

Nightly defaults: **~1.4x "safe"** and **~2.0x "reach"**, so a paper record builds at both risk levels.

### 15.4 Critical data-layer finding (would silently break the build if missed)

`edges.side` / `edges.implied_prob` hold the side the **model** prefers (chosen by max edge — `modeling/edges.py` L92–95), which may be an *underdog the model likes*. The builder wants the **favorite** side, which is a *market* question.

**So the builder devigs the raw two-sided odds itself** from `prop_lines` / `game_lines` (reusing `modeling.edges.devig()` / `odds_to_probability()`) and picks the favorite. It touches `edges` / `game_edges` **only** to left-join `model_prob` as display context.

> `edges` is model-centric. The builder is market-centric. **Never rank on `edges.implied_prob`.**

### 15.5 Architecture — Approach 1: new unified `optimizer/builder.py`

- **Leg loading**: latest `prop_lines` (13 MLB player-prop stats) + latest `game_lines` (team NRFI + F5), via the same `DISTINCT ON … ORDER BY pulled_at DESC` pattern as `edges.py:latest_prop_lines`. Devig both sides, take the **favorite**, keep only if `market_prob ≥ floor (0.55)`. Skip one-sided lines (~8% of live MLB lines can't be devigged — same guard as `edges.py`). Games not yet `FT`. Player and team legs normalize into **one common schema**: `{game_id, kind: 'player'|'team', label, stat_type|market, side, line_value, american_odds, decimal_odds, market_prob, model_prob (nullable, context)}`.
- **Search**: across-game combinations of size **2–4**; a combo with two legs sharing a `game_id` is skipped (this exclusion is what makes the independent product valid). Per combo `combined_odds = Π decimal_odds`, `joint_prob = Π market_prob`. Note the existing `find_combinations` ranks on `model_prob` — generalize it to take a probability key or write the search fresh, but **preserve the tested same-game-exclusion behaviour** either way.
- **Two-axis filter/rank**: pin payout → filter to the band, sort by `joint_prob` desc. Pin probability → filter `joint_prob ≥ floor`, sort by `combined_odds` desc. Both pinned → filter both, sort by `joint_prob` desc. Return top-N.
- **Combinatorial safety (fixes the nightly OOM)**: cap the candidate pool so `C(N, max_legs)` ≤ ~5M. With `max_legs=4` this needs a tighter `N` than the old max-3 cap of 200 — `C(200,4)` ≈ 64.6M would OOM again. Keep the highest-`market_prob` legs when capping; the 0.55 floor already shrinks the pool a lot.
- **Persistence**: top-N into `parlay_recommendations` reusing `team_parlay.py`'s JSONB wrapper `{class: 'across_game', legs: [...]}` — but **drop the `ev` field** (no EV claim). Per-leg JSONB carries `market_prob`, odds, side, label, `model_prob`.
- **Reuse, don't duplicate**: `american_to_decimal`, `devig`/`odds_to_probability`, the same-game exclusion. `optimizer/parlay.py` and `optimizer/team_parlay.py` leave the daily chain but stay in-tree for helpers and as the tested substrate for the same-game v2.

### 15.6 API (stage 1) and dashboard (stage 2)

**API** — new read-only, additive-only endpoint behind the existing API-key dependency:

```
GET /parlay-builder?target_payout=&min_prob=&max_legs=&floor=&sport=mlb
→ [{ legs: [{game_id, kind, label, side, line, odds, market_prob, model_prob}],
      combined_odds, joint_prob, n_legs }]
```

No `ev` field. Must **not** modify `/edges`, `/parlay-recommendations`, `/game-predictions`, `/box-scores`, `/games` — the Budgerr contract (§7.1) is additive-only.

**Dashboard** — new page in `web/` matching `web/app/edges/` conventions and DESIGN.md (near-black surface, one signal-green accent, Geist Sans/Mono); read PRODUCT.md + DESIGN.md and `web/AGENTS.md` (Next 16 caveats) first. Two controls (target payout, minimum joint probability) — pin either. Each result shows its **joint probability front and centre** ("≈ X% to hit") as the most prominent number on the card; per leg show `market_prob` (authoritative) and `model_prob` (muted, "model — not used for ranking"). Honest framing copy explaining what joint probability means and that ~2x ≈ a coin flip.

### 15.7 Daily chain + paper tracking

In [`scripts/daily_chain.sh`](scripts/daily_chain.sh), replace the `optimizer.parlay` step with `python -m optimizer.builder` at the two default targets (~1.4x, ~2.0x), capped. `modeling.settle` already runs later in the chain, and the existing dashboard "Betting record (paper)" section then shows the builder's real W-L-P / ROI.

> **Correction (2026-07-21):** an earlier draft of this section claimed settlement needed **no new code**. That is **wrong**. Both existing paths are *homogeneous*: `settle_parlays` (`kind='player'`) requires `player_id` on every leg and reads `player_game_stats`/`prop_lines`, while `settle_team_parlays` (`kind='team'`) requires `market` on every leg and reads `team_game_stats`/`game_lines`. The builder's whole premise is **mixing player and team legs in one parlay**, which crashes both. Stage 1 therefore adds `settle_builder_parlays()` (`kind='builder'`), dispatching **per leg** on `leg["kind"]`. The pure scoring functions (`settle_leg`, `parlay_result`, `_rec_snapshot`, `american_to_decimal`) are all reused, so it is contained — but it is real work, not free.

This **retires the step that OOM-died (SIGKILL) nightly** and pushed a false failure alert every morning (§11 chain caveat) — the cap plus the 0.55 floor is the fix, and the runaway step goes away rather than being patched.

### 15.8 Testing + guardrails

Pure-math unit tests following `tests/test_parlay.py` (DB-free, runs under `env -i`, added to CI): favorite-side selection from two-sided odds (**including the case where the model prefers the underdog**), 0.55 floor filtering, the two-axis filter (pin payout / pin probability / both), `joint_prob` and `combined_odds` as exact products, across-game exclusion, the candidate cap actually bounding `C(N, max_legs)`, player+team leg normalization, and the one-sided-line skip.

**Guardrails — do not violate:**
1. Rank **only** on devigged market probability; never on `model_prob`.
2. **No "+EV" / "edge" / "value" / "beat the market" claims** in UI, API payloads, or recommendation JSONB.
3. Always surface joint probability prominently — it *is* the risk.
4. Favorite-side legs only, `market_prob ≥ floor`.
5. Across-game only (independent) in v1.
6. **No real-money deployment.** Honest constructor + paper-trading sandbox.

### 15.9 Future work (deferred, priority order)

1. **Same-game combos** — the user wants these "if possible," explicitly deferred to future work. Start with NRFI+F5 via the empirical lift already built and tested in `modeling/correlation.py` + `optimizer/team_parlay.py:same_game_pairs`, surfaced as a separate labelled class showing its **sample size**. Then player+team and player+player same-game correlation (§14.2 — copulas over discrete marginals are non-unique; opus-grade, don't front-load). Only trustworthy with ~a season of shared history.
2. **Improve model resolution** — user explicitly wants this kept on the radar. The builder avoids depending on the models, but improving them would let `model_prob` graduate from context to a real filter. Per §11 this needs *genuinely new predictive data* (park factors, weather, umpire, lineups, deep pitcher/bullpen stats), **not tuning** — making the model stronger made R² *worse*. Gated by §11's permanent acceptance test: `corr`/R² of predicted-vs-actual well above zero **and** `predicted_mean` tracking the book line with slope → 1.
3. **Line shopping / best-price legs** (§14.2) — the builder ranks on consensus devigged prob, but SGO already returns a `byBookmaker` breakdown that `odds_ingest.py` discards. Best available price per leg strictly improves payout at fixed risk: cheapest real improvement, zero modeling, zero extra quota.
4. **Kelly stake sizing** (§14.2) — ¼-Kelly per parlay-as-one-bet plus a same-night total-exposure cap.

### 15.10 Stage 1 — BUILT 2026-07-21 (engine + API). Dashboard still unbuilt.

Built via [the stage 1 plan](docs/superpowers/plans/2026-07-21-low-risk-parlay-builder-stage1.md): `optimizer/builder_core.py` (pure math), `optimizer/builder.py` (DB loading, persistence, CLI), `settle_builder_parlays()` in `modeling/settle.py`, and `GET /parlay-builder`. **173 pytest cases green.** Verified end-to-end against live data and on spare port 8099 (never the live `:8000`).

**Two spec bugs were caught by verification, not by tests** — both are recorded because they are the kind of thing that would silently recur:

1. **Settlement was not free** (§15.7 correction above). The two existing paths are homogeneous and neither can score a mixed player+team parlay. Fixed with `settle_builder_parlays()`, dispatching per leg on `leg["kind"]`.
2. **The candidate cap made the 2x goal impossible.** The plan's `cap_candidates` kept the top-N legs by `market_prob` — i.e. the most extreme favourites — which collapsed the odds ceiling: all 673 legs spanned 7 games with odds 1.002–1.730 (max 4-leg payout **8.54x**), but after the cap only 5 games survived with odds 1.002–1.104 (max **1.43x**). A 2.0x target was therefore unreachable and the builder returned nothing. The engine was correct; the *spec* was wrong.

**The search that replaced it** (all exact, no lossy heuristic): per-game price dedupe (same price ⇒ same payout, so keep the most probable leg); a **game-structured DFS** that picks a set of games and then one leg from each, so same-game pairs are never generated at all — the old flat enumeration built `C(673,4)` ≈ **8.5 billion** tuples almost entirely to discard them; exact pruning on both bounds (odds only grow, so anything past the ceiling is dead; a suffix-maximum bound kills branches that can't reach the floor); and a **bounded top-N heap**, because the first pass held 3.4M result dicts in memory and recreated the very OOM this builder exists to eliminate.

**Live behaviour, 2026-07-21 slate:** 673 candidate legs (670 player + 3 team) across 7 games. Pinning **75% probability → 1.26x**, searched exhaustively (1.1M nodes, no truncation) — real-data confirmation of §15.2's claim that genuine low risk is ~1.3–1.5x, not 2x. Pinning **2.0x** returns 2-leg constructions at ~55% to hit.

**Known limitations (honest, unfixed):**
- **The search truncates on a full slate — at both defaults, not just 2.0x.** It hits its 5M-node budget and reports `WARNING: search hit its node budget — results are partial, not exhaustive`. This was originally observed only on the 2.0x search against a thin 7-game/673-leg slate; on the fuller 19-game/2,443-leg slate typical of a real night, **both the 1.4x and 2.0x searches truncate.** 2-leg constructions are searched first and are provably the safest route to any payout (least vig), so the top results are almost certainly near-optimal — but this is not proven, and the warning is deliberately surfaced rather than hidden.
- **RESOLVED 2026-07-21 (user-confirmed): a pinned target payout is a FLOOR, not the centre of a tolerance band.** The bug: `lo/hi = target*(1∓tolerance)`, ranked by `joint_prob` — which falls monotonically as payout rises, so ranking inside a symmetric band *always* returned the band's bottom edge. `--target-payout 1.4 --tolerance 0.10` returned 1.26x; `--target-payout 2.0 --tolerance 0.10` returned 1.80x; the API's default tolerance (0.15) returned 1.73x for a 2.0x request. The tolerance was silently the real target. Fixed by making the two axes exact duals: pin `min_prob` → filter `joint_prob >= min_prob`, rank by `combined_odds` (unchanged); pin `target_payout` → filter `combined_odds >= target_payout`, rank by `joint_prob` (**new**: floor, not band). `tolerance` now means "initial search ceiling above the floor" — a *performance* knob, not a correctness one: if nothing qualifies within it, the search **progressively widens** (1.5x, 3x, then unbounded) until it finds the cheapest qualifying construction. This is exact, not a heuristic — decimal odds are all ≥ 1, so widening only ever adds candidates with strictly higher payout and therefore strictly lower joint probability, and a wider pass can never displace a result a narrower one already found. Verified live: both `--target-payout 1.4` and `--target-payout 2.0` (`--tolerance 0.10`) now return only constructions at or above the requested payout.
- **Team legs are thin** (3 of 673 on the 2026-07-18 slate). `game_lines` holds 107 NRFI + 45 F5 lines but was last pulled 2026-07-20, so few map to unfinished games. The player+team mixing path is structurally exercised and correct; it is data freshness, not a code gap.
**Two further production bugs, found only by running the real write path** (both would have failed the nightly chain *every night*, and both passed their unit tests):

3. **`--save` crashed on invalid JSON.** `model_prob` arrives as `NaN` from the `LEFT JOIN` when no `edges` row exists — pandas keeps `NaN` in a float column rather than `None`, and `json.dumps` emits a bare `NaN`, which Postgres rejects (`InvalidTextRepresentation`). Exit code 1 in an `&&` chain = a failure alert every morning. Fixed by coercing NaN→None in `_clean_optional` (pure-Python `value != value`, no pandas import) and by passing `allow_nan=False` so any future NaN raises a loud Python error instead of a confusing database one.
4. **Settlement crashed the moment real builder rows existed.** `_as_legs_list` handled `list` and `str` but not `dict` — psycopg2 returns JSONB **already parsed**, so the `{"class", "legs"}` wrapper arrived as a dict and `json.loads(dict)` raised `TypeError`. It had passed its tests and no-op'd cleanly while zero rows existed. **The dormant team path (`settle_team_parlays`) shares this exact shape and would have hit the same crash the moment it went live** — fixed for both.
5. **`GET /parlay-recommendations` 500s the moment a builder row enters its result window.** Same root cause as bug #4 (`json.loads(dict)` on an already-parsed JSONB wrapper), never fixed here because the fix for #4 only touched `modeling/settle.py`. It passed every one of its (zero — none existed) tests and was masked in production purely by the legacy `optimizer.parlay` chain step writing newer `kind='player'` rows on top of the builder's; the moment that step is retired (tonight — §15.7) the 10 newest rows are all `kind='builder'` and the endpoint's *default* `limit=10` 500s. Verified live against the running system: `limit=10` returned 200 only because of the stale legacy rows above it in the window, `limit=20` already reached the builder rows and returned 500. This endpoint is external-contract surface (Budgerr, §7.1) — its Tonight view would have gone down the first morning the builder ran alone. Fixed by (a) restricting the query to `WHERE kind IN ('player', 'team')` — builder constructions stay exclusive to `/parlay-builder`, which has its own schema for mixed player+team legs; (b) fixing the same dict-wrapper unwrap for the dormant `kind='team'` shape, which shares it; (c) making `player_id`/`stat_type` on `ParlayLeg` `Optional` (additive widening, not a removal) since the team shape has neither — without this, a genuine future `kind='team'` row would still 500 with a Pydantic validation error even after (a) and (b). `ParlayRecommendationOut` and every currently-served (player-kind) field/value are unchanged.

**Daily chain — SWAPPED 2026-07-21.** `scripts/daily_chain.sh` now runs `optimizer.builder` at `--target-payout 1.4` and `2.0` (both `--tolerance 0.10 --top-n 5 --save`) in place of the OOM-prone `optimizer.parlay` step. Verified live: both saves exit 0 and insert 5 rows each; `modeling.settle` then correctly reports "0 new builder parlays (10 not yet ready)" for unfinished games. Runtime ~15s per target.

**Paper-trading record as of 2026-07-21** (worth reading against §8's single-slate "+0.8% ROI", which was noise): all-time **2236-1703-0, −281.56u, ROI −7.1%** (edges −6.6% over 3,885 bets; the old model-ranked parlays −49.0% over 54). With a real sample, the model-driven bets are **clearly losing**, which is exactly what §11's resolution finding predicts and is the strongest argument yet for ranking on market probability instead. The builder's own rows are unsettled so far — its record starts accumulating from the 2026-07-21 slate.

**Paper-ledger split — user-confirmed 2026-07-21.** `settle_builder_parlays()` writes to `recommendation_outcomes` with `bet_type='parlay'` — the same DB value the legacy model-ranked parlays and the team-parlay path use, since the `bet_type` CHECK constraint only allows `('parlay','edge')`. Left unaddressed, the builder's first settlements (landing the night of 2026-07-21) would have pooled into the same losing 16-48-0 / −57% ROI bucket as the legacy parlays, making the builder's own record unreadable from day one. **No migration, no constraint change** — the distinction is already derivable: every `recommendation_outcomes.parlay_id` FKs to `parlay_recommendations.parlay_id`, which carries `kind` (`'player'` for the legacy parlays, `'team'`, `'builder'`). `GET /bet-performance` and `modeling.settle.print_summary()` now both LEFT JOIN onto `parlay_recommendations.kind` and report `parlay_model` / `parlay_team` / `parlay_builder` in place of one pooled `parlay` row; `edge` and the combined `all` row are unaffected. The three `settle_*_parlays()` dedupe guards were re-verified safe: `parlay_recommendations.parlay_id` is one `SERIAL` PK shared across all three `kind`s, and each function's candidate query already filters on its own `pr.kind`, so no parlay_id can ever be a candidate for more than one of them — the shared `bet_type='parlay'` `NOT EXISTS` guard was never actually at risk of a cross-kind collision.
