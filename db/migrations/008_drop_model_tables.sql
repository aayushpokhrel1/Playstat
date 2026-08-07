-- Migration 008: drop the shelved model + F5 tables (README §16 / roadmap #3B).
-- The MLB player prediction model and the dormant F5 team-market model were
-- shelved 2026-07-29 (§16) and DELETED 2026-08-06 (#3B, user-approved): their
-- code, the serving endpoints (/edges, /game-predictions, /parlay-recommendations
-- removed in #3A; /edge-distributions, /model-performance, /players/{id}/
-- predictions, /backtest-history, /clv-summary removed in #3B Phase 1), and now
-- their tables. The market-ranked low-risk builder never used any of these
-- (model_prob was a context-only LEFT JOIN off `edges`, now always None).
--
-- KEPT (not dropped): recommendation_outcomes (the paper-trading ledger, incl.
-- the historical edge/model/team rows) and parlay_recommendations (builder rows
-- + frozen model rows). Both are still read by the builder settle path +
-- print_summary / /bet-performance.
--
-- A full pg_dump (schema + data) of these six tables was taken immediately
-- before this migration (architect scratchpad, 2026-08-06). To resurrect: run
-- that dump, then recover the deleted modeling/* modules from git history.
BEGIN;
DROP TABLE IF EXISTS model_predictions;
DROP TABLE IF EXISTS edges;
DROP TABLE IF EXISTS backtest_runs;
DROP TABLE IF EXISTS clv_records;
DROP TABLE IF EXISTS game_predictions;
DROP TABLE IF EXISTS game_edges;
COMMIT;
