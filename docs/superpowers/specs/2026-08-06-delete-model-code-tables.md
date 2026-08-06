# Delete the shelved player-prediction model — code + tables (roadmap #3B) — Design Spec

**Date:** 2026-08-06
**Status:** APPROVED — user confirmed "delete the model code + tables" AND "also delete F5" (2026-08-06). Execute code phases (reversible) then the destructive live-table migration (pg_dump backup first).
**Decision:** User chose "delete the model code + tables" (2026-08-06), after #3A landed, and extended scope to **also sweep the dormant F5/team-market model**.
**Prior context:** README §16 (model shelved 2026-07-29), §11 (resolution finding; team-market F5 build kept as "substrate"), §15.9 #1 (NRFI+F5 same-game combos earmarked via `correlation.py`/`team_parlay.py`), §7.1 (Budgerr — now off `/edges` etc.).

## Scope

**IN — the shelved PLAYER prediction pipeline and its edge/CLV serving surface** (this is exactly the (B) brief's scope, plus the adjacent files that only exist to serve it):
- Player model pipeline: `modeling/features.py`, `predict.py`, `predict_upcoming.py`, `train.py`, `backtest.py`, `calibration.py`, `eval_discrete.py`, `distributions.py`.
- Edge + CLV serving: the model-edge parts of `modeling/edges.py` (KEEP `devig`/`odds_to_probability` — see below), `modeling/clv.py`.
- Tables (drop, backup first): `model_predictions` (157,126), `edges` (21,596), `backtest_runs` (258), `clv_records` (21,596).
- API endpoints (remove): `/edge-distributions`, `/model-performance`, `/players/{id}/predictions`, `/backtest-history`, `/clv-summary`.
- Web: the predictions-vs-actuals section of `web/app/players/[id]/page.tsx` (+ `getPlayerPredictions` in `web/app/lib/api.ts` + the `Prediction` type) — the only UI touched.
- `settle.py`: delete `settle_parlays`, `settle_team_parlays`, `settle_edges`, `team_leg_actual`, `_rec_snapshot` (VERIFY only-model use during exec); shrink `settle()` to `settle_builder_parlays` + `print_summary`.
- `scripts/daily_chain.sh`: remove the `clv` step and the commented `features`/`predict`/`edges`/`backtest` block + restore marker.
- Tests: delete `test_distributions.py`, `test_pmf.py`, `test_train_helpers.py`; prune model-path tests from `test_settle.py`, `test_odds.py` (keep the devig tests, retarget import), `test_calibration`/`test_backtest`/`test_eval_discrete` if present.

**IN (F5 extension, user-confirmed 2026-08-06 "also delete F5"):**
- `modeling/f5.py` (F5 predictive model), `modeling/team_edges.py` (imports `f5`, writes `game_edges`).
- Tables (drop): `game_predictions` (408), `game_edges` (14).
- Tests (delete): `test_f5_model.py`, `test_f5_reconstruct.py`, `test_team_edges.py`, `test_settle_team.py`.
- `settle.py`: `settle_team_parlays` + `team_leg_actual` deletion (already in scope) covers the F5 team-parlay settlement.
- Note: `game_predictions`/`game_edges` frozen since the shelving; the /game-predictions endpoint that read `game_predictions` is already removed (#3A).

**OUT — kept deliberately (NOT deleted):**
- **`devig` + `odds_to_probability`** — the market-ranked builder (`optimizer/builder_core.py:9`) depends on them. **Extract** to a new builder-owned module (`optimizer/devig.py`) and update `builder_core.py` + `test_odds.py`/`test_odds_nfl.py` imports. This is the one refactor, not a deletion.
- **`modeling/correlation.py`** — pure; reads `team_game_stats` `runs_f5`/`runs_inning_1` **actuals** (not the F5 model, not the dropped tables), so unaffected by the F5 deletion. Earmarked for #2 same-game combos (§15.9 #1). Keep. `runs_f5` is a `team_game_stats` stat_type (observed data from ingestion), NOT a table/model — untouched.
- **`optimizer/team_parlay.py`** — imports only `correlation` + `parlay` (imports resolve after F5 deletion). Preserves `same_game_pairs` for #2 (§15.9 #1). Its `load_team_legs` reads the now-dropped `game_edges` at runtime → that path becomes **dormant/stale**; documented in §11/§16 so #2 re-sources team legs from `game_lines` (as the builder already does). Its DB-free tests (`test_team_parlay.py`) stay green.
- **`recommendation_outcomes`** (13,112) + `parlay_recommendations` (256) — the paper-trading ledger + builder rows. Keep fully (history + builder-live). `print_summary`/`aggregate_bet_performance` read only `recommendation_outcomes`.
- **All `settle.py` leg-helpers** (`settle_leg`, `settle_spread_leg`, `settle_moneyline_leg`, `game_total`, `leg_status`, `parlay_result`, `single_pnl`), `builder_leg_key`, `bet_type_label`, `_as_legs_list` — builder-shared.
- **`modeling/first_inning.py`** — feeds builder team markets (`game_lines`); already active in the chain.

## Keep/Delete table (files)

| Path | Action | Why |
|---|---|---|
| modeling/features.py, predict.py, predict_upcoming.py, train.py, backtest.py, calibration.py, eval_discrete.py, distributions.py | DELETE | player model, no builder/settle use |
| modeling/edges.py | SHRINK → extract devig/odds_to_probability to optimizer/devig.py, delete rest | devig is builder-shared |
| modeling/clv.py | DELETE | reads `edges` table (model-edge CLV) |
| modeling/f5.py, team_edges.py, correlation.py, optimizer/team_parlay.py | KEEP | §11 substrate / §15.9 #1 earmark |
| modeling/settle.py | SURGERY (delete 4 model fns, keep builder + helpers) | builder-critical |
| modeling/first_inning.py | KEEP | builder team-market feed |
| optimizer/builder*.py, parlay.py | KEEP (update builder_core devig import) | the product |
| api/main.py | remove 5 endpoints + now-dead imports | model serving |
| web/app/players/[id]/page.tsx, lib/api.ts | remove predictions section | reads model_predictions |

## Migration (RESERVED LANE — architect only)

New migration `db/migrations/0XX_drop_model_tables.sql`: `DROP TABLE IF EXISTS model_predictions, edges, backtest_runs, clv_records, game_predictions, game_edges;` (F5 tables included per the user's "also delete F5"). **Backup first**: `pg_dump` those six tables to a timestamped file in the scratchpad before dropping. Verify row counts pre-drop, run, verify tables gone, run the chain's `settle`/builder steps against the live DB to confirm no breakage.

## Phasing (all reversible until the migration)

1. **Extract devig** → `optimizer/devig.py`; update `builder_core.py`, `clv.py`(about to be deleted anyway), `test_odds.py`, `test_odds_nfl.py`. Run tests. Commit.
2. **Delete model code + settle surgery + chain edit + tests**; keep everything green (the builder + settle_builder path + devig tests). Commit.
3. **API + web**: remove the 5 endpoints + dead imports; remove the web predictions section. `tsc`/`next build` clean. Commit. Kickstart API.
4. **Live migration** (backup → drop) — architect reserved lane, after user go. Commit the migration file + README updates (§7.1/§8/§11/§16). Push.

## Risks / gates

- **Builder must stay byte-identical.** The exactness oracle `tests/test_builder_search_exactness.py` + all builder/settle-builder tests must stay green after the devig extraction. `MAX_NODES`/guardrails untouched.
- **Settlement must still run.** After `settle()` surgery, the nightly `settle` step must settle builder parlays and print the ledger unchanged (reads `recommendation_outcomes`).
- **No Budgerr surface touched** — `/parlay-builder/saved`, `/games`, `/box-scores` unchanged (Budgerr already off the model endpoints, #3A).
- **Irreversibility** — only the table DROP; mitigated by pg_dump backup. Code is git-recoverable. Historical migrations are NOT edited (a new forward migration drops).
