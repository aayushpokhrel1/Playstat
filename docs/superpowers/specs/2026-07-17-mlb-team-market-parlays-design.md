# MLB Team-Market Parlays (NRFI + F5) — Design

**Date:** 2026-07-17
**Author:** architect session
**Status:** design approved, pending spec review → implementation plan

## Motivation

The player-prop pipeline has a **structural bias discovered 2026-07-17**: across all 326
players with ≥30 games, the regression slope of the model's `predicted_mean` on each
player's own season rate is only **0.18–0.85 depending on stat (mostly ~0.3–0.45)** —
the XGBoost models shrink every player toward the league average and capture barely a
third of real player-to-player variation. Per-stat `avg_actual ≈ avg_pred`, so the model
is *calibrated on average* while having almost no *resolution* across players — which is
why MAE/PIT backtests never caught it. Betting lives entirely on the resolution axis the
model is weakest on. The `edge > 3%` filter then selects precisely the players where the
model most disagrees with the market (elite base-stealers, etc.), i.e. where the model is
most wrong — adverse selection, not edge. Recomputing the 2026-07-16 slate's parlays with
each player's realized rate collapsed a claimed +35–38% EV to +4% average, 4 of 10
negative.

Team-level markets do **not** live on the player-discrimination axis, so they dodge this
failure mode. This project pivots MLB betting to two team markets and **shelves player
props** until the player models earn real resolution.

## Scope (user-confirmed 2026-07-17)

- **Markets:** two **game totals** (both teams' runs combined), over/under:
  - **NRFI / 1st-inning runs** — already modeled (`modeling/first_inning.py`, `xgb_fi_v2`).
  - **F5 / first-5-innings runs** — new.
- **Goal (layered):** ship a *safest-path-to-~2x builder* that always produces output,
  and *tag each recommendation with model-vs-market EV* so a genuine edge is visible when
  the model has one. No blanket +EV promise — model prob is a fair-price estimate.
- **Combination, two classes kept separate:**
  - **Across-game** (default): each leg NRFI-or-F5 from a *different* game; joint prob =
    product (independence valid).
  - **Same-game NRFI+F5 pairs**: joint prob from an **empirical co-occurrence table**, never
    naive multiplication — inning 1 is nested inside innings 1–5 and positively correlated.
    Always exactly 2 legs, separately labeled.
- **Payout shape:** target **2.0x decimal, ±15% tolerance, 2–3 legs** (reuse the existing
  optimizer's settings). Same-game pairs are naturally 2-leg.
- **Player props:** **hard stop** — pulled from the dashboard UI and removed from the daily
  chain. Models/distributions/tests stay in the repo and DB (reversible); §11 records the
  shrinkage finding as the reason.
- **Budgerr contract:** **coordinated redefinition**, not additive-only. Budgerr does not yet
  consume `/edges` or `/parlay-recommendations` (§11), so this is the moment to redefine them
  around team markets — gated on a proposal message to the Budgerr session first.

## Feasibility (verified 2026-07-17)

- **F5 outcomes — free, no new API.** `ingestion/mlb_backfill.py:230` already sums *all*
  innings from the StatsAPI `linescore` hydrate for full-game `runs`. F5 = `sum(innings[0:5])`
  from the same already-fetched response. Backfill = add one field + re-run `--only
  linescores` (one hydrated request per season). This also supplies the correlation-table
  history.
- **F5 lines — RESOLVED (probed live 2026-07-17).** F5 is period **`1ix5`** in SGO (the "innings
  1 through 5" cumulative family: `1ix3`/`1ix5`/`1ix7`), statID `points`, entity `all`. **Fully
  priced on our free tier** — a probe of one MLB event returned line 3.5, over +110 / under −146,
  with a `byBookmaker` breakdown across fanduel/bovada/betmgm/draftkings. `GAME_MARKETS["mlb"]`
  now carries `"f5_runs": ("points","all","1ix5")`. So F5 is fully priced and EV-taggable; the
  paid-tier and model-only contingencies did not trigger. **One design consequence:** unlike
  NRFI's fixed 0.5 line, F5 lines vary per game, so the F5 model predicts a mean and derives
  P(under actual_line) per game rather than training a single fixed-threshold classifier.

## Architecture

### Data layer
- **Migration `NNN_team_markets_f5.sql`** (next free number at apply time — `006` if the held
  line-shopping branch's `005` lands first, else `005`; the architect assigns it to avoid a
  collision): `team_game_stats.runs_f5` (numeric, nullable);
  `parlay_recommendations.kind` (text, default `'player'`) discriminator; whatever the
  correlation table needs (see below). Applied to live DB by the architect only.
- **F5 backfill**: extend the linescore loop; recompute for all seasons via `--only
  linescores`. Free.
- **NRFI×F5 correlation table**: an empirical joint built from historical box scores —
  P(NRFI over/under ∧ F5 over/under) per game, at the lines actually quoted. Stored (small
  table or a computed artifact) and refreshed as history grows. Known bias: thin-sample noise
  until a season of shared history accumulates.

### Model layer
- **`modeling/f5.py`** (sibling to `first_inning.py`; proven pattern, low risk). Target:
  total F5 runs (both teams) as a count, mean ~4.5 → far less zero-inflated than NRFI, so a
  cleaner NB2 fit. Features: each team's rolling F5 runs scored/allowed + **both starters'
  form** (already ingested for the first-inning model). F5 ≈ "the starters' game" (bullpen
  largely excluded), so starter features are directly on-point — F5 has a better shot at real
  resolution than NRFI (too small-sample) or full-game (bullpen variance).
- **Honesty gate**: report F5's holdout Brier vs the always-predict-base-rate baseline in the
  spec/README, exactly as §13.3 does for NRFI (which is currently *at parity*). The optimizer
  treats model prob as a fair-price estimate, never a guaranteed edge.
- `game_predictions` already stores `(game_id, market, predicted_mean, prob_under, prob_over,
  line_value, model_version)`; F5 is a new `market` value. `STAT_CONFIG`/train wiring follows
  the first-inning precedent.

### Optimizer — `optimizer/team_parlay.py`
Reuses the **unit-tested** pure helpers in `optimizer/parlay.py` (`american_to_decimal`,
`find_combinations`, `joint_prob`, `combined_odds`).
1. **Candidate legs**: for each upcoming game, NRFI over/under and F5 over/under, each with
   model prob + book odds (F5 odds only if lines available).
2. **Across-game builder**: `find_combinations` with same-game exclusion (already implemented),
   ranked highest-joint-prob near 2.0x ±15%, 2–3 legs.
3. **Same-game pairs**: separate path; joint prob from the empirical correlation table; exactly
   2 legs; labeled distinctly.
4. **EV tag** per recommendation: `joint_prob × combined_odds − 1`, using de-vigged market
   probabilities where lines exist.

### Serving / settlement
- Reuse `parlay_recommendations` with the `kind` discriminator (`'team'` new, `'player'`
  legacy) so the ledger and dashboard stay uniform.
- **`modeling/settle.py`** gains a per-leg-type resolver: team legs settle from
  `team_game_stats` (`runs_inning_1`, `runs_f5`) vs the FT status of the game; player legs
  keep resolving from `player_game_stats`. Same idempotent NOT-EXISTS, FT-only, paper-stake
  semantics.
- **API**: redefine `/edges` + `/parlay-recommendations` around team markets, coordinated with
  Budgerr (proposal message first). A team-parlay serving endpoint mirrors the existing shape.
- **Dashboard**: pull player-prop edges/parlays from the UI; add the team-parlay view
  (across-game and same-game sections). Read PRODUCT.md + DESIGN.md; match `web/app/edges/`
  conventions.

### Daily chain
Replace the player-prop `modeling.edges` + `optimizer.parlay` steps with the team-market
pipeline: F5 predict → team-line ingest → NRFI+F5 edges → `optimizer.team_parlay`. Keep
`modeling.settle` (now team-aware) and the heartbeat wrapper from `scripts/daily_chain.sh`.

## Testing
Pure-math first, per repo convention (§14.1): correlation-table joint-prob math, the
across-game/same-game combination logic, EV computation, F5 distribution reconstruction, and
the team-leg settlement resolver — all DB-free with synthetic inputs. F5 model quality is
reported as a holdout Brier-vs-baseline number, not asserted.

## Phasing (for the implementation plan)
- **Phase 0** — SGO F5-line probe (gate; determines whether F5 is priced or model-only).
- **Phase 1** — data: migration 006, F5 backfill, correlation table.
- **Phase 2** — F5 model + holdout honesty report.
- **Phase 3** — `team_parlay` optimizer + EV tag + team-aware `settle.py`.
- **Phase 4** — serving: API redefinition + Budgerr coordination + dashboard (add team, remove
  player) + daily-chain swap + README §11/§13/§14 updates.

## Non-goals / deferred
- No player-prop model fix here (the shrinkage fix is its own future effort; player props
  stay shelved, not deleted).
- No Kelly staking, line-shopping, or same-game correlation *modeling* beyond the empirical 2×2
  (those remain their own §14 items).
- F5 per-team totals and F5 moneyline/run-line are out of scope (game totals only).

## Open risks
- **F5 lines may not be free** (Phase 0 resolves; escalate paid plan to user if so).
- **NRFI is at parity** and F5 may also turn out low-resolution — the layered design tolerates
  this (builder still works; EV tag stays honest), but if both models are near-baseline the
  *edge* value of the feature is limited and that must be stated plainly, not papered over.
- **Correlation table is thin** early; same-game pairs carry more estimation noise until a
  season of shared NRFI/F5 history accumulates.
