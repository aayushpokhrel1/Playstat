# Incremental feature upsert (chain perf — the real bottleneck)

Spec-complete. All design decisions LOCKED. Read README §15.9 item 7 (context:
the ~7-8h chain runtime; profiling on 2026-07-24/25 localized it).

## The finding (measured, not assumed)

`modeling/features.py` `compute_features` COMPUTE is ~77s and model training is
~2s/stat — neither is the bottleneck. The cost is the nightly **upsert of ~2.3M
`rolling_player_features` rows** (`INSERT ... ON CONFLICT DO UPDATE`): confirmed
live 2026-07-25 as **~2h+ and growing** (the `features` step ran >2h18m while
every other step finished in ≤28s). It re-writes IMMUTABLE historical rows: a
player's `shift(1).rolling(w).mean()` as-of a PAST game depends only on games
strictly before it, which never change once played. So re-upserting all of
history nightly is pure, growing waste.

## The fix (LOCKED design)

Keep the COMPUTE full (it's only 77s, so full compute guarantees identical
values — zero correctness risk). Make only the UPSERT incremental: upsert only
rows whose `as_of_date >= (today - LOOKBACK_DAYS)`, skipping older immutable
rows already in the table.

- Rows upserted each night: all UPCOMING rows (`as_of_date` in `[today, today+upcoming]`,
  which shift nightly and MUST be refreshed) + recently-played games
  (`[today - LOOKBACK_DAYS, today]`). ~a few thousand rows, not 2.3M.
- Rows skipped: `as_of_date < today - LOOKBACK_DAYS` — immutable, already stored,
  provably identical to a recompute.
- `LOOKBACK_DAYS` default **7** (covers same-day box scores + a generous late-arrival
  margin). A `--full` flag forces the current full-history upsert (for one-time
  rebuilds / the weekly safety net below).

## Changes

1. `modeling/features.py`:
   - `compute_features(engine, sport="nba", upcoming_days=0, lookback_days=7, full=False)`.
     Compute unchanged. Before the upsert, if `not full`, filter `values` to
     `row["as_of_date"] >= date.today() - timedelta(days=lookback_days)`. Keep the
     existing single batched `INSERT ... ON CONFLICT` — it's now tiny.
   - Print the kept-vs-total counts so the log shows the reduction, e.g.
     `upserted 4,120 of 2,294,638 computed rows (incremental, lookback 7d)`.
   - CLI: add `--lookback-days` (default 7) and `--full` (store_true).
2. `scripts/daily_chain.sh`: the nightly `features` step stays incremental (default).
   Do NOT add a `--full` there — the architect will schedule a periodic (weekly)
   `--full` rebuild separately as the immutability safety net (out of scope here).

## Tests (`tests/`)

CRITICAL SAFETY: there is NO test DB; `ingestion.db.get_engine()` is LIVE. New
tests MUST be pure (no engine). See `tests/test_builder.py` for the convention.
- Pure filter test: given a `values` list spanning old + recent + upcoming
  `as_of_date`s and a `lookback_days`, exactly the rows `>= today - lookback_days`
  are kept, and `full=True` keeps all. Factor the cutoff filter into a small pure
  helper (e.g. `_incremental_cutoff(today, lookback_days)` / a pure
  `_filter_values(values, cutoff)`) so it is unit-testable without a DB.
- Assert the COMPUTE path is untouched (values for a given player-game are
  identical whether or not the filter runs — the filter only drops rows, never
  changes them).
- Full suite stays green (currently 225).

## Verification (read-only, safe against the live engine)

Include a read-only script (do NOT write the live DB) that proves equivalence:
compute the full `values`; for a sample of historical rows (`as_of_date < cutoff`),
assert the value stored in `rolling_player_features` already equals the freshly
computed value (immutability holds → skipping is exact). Report the match rate
(must be 100%). This is the correctness gate; report it in your summary.

## Out of scope (architect does these)

- Deploying the chain change / any live `--full` rebuild scheduling.
- `launchctl` / git push. Work in the worktree, commit there only, never touch
  `:8000`, launchd, or the live DB write paths.
