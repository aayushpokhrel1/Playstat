# Builder independence (diverse top-N) — design spec

Own sub-project (not part of NFL #3). Follows the §15 discipline: brainstorm →
this spec → plan → delegate → review → verify → merge. User-confirmed
2026-07-29.

## Problem (measured, not assumed)

The builder saves the **top-N constructions by joint-probability** per (tier,
target). At a payout floor those top-N are assembled from the same tiny pool of
safest favorite legs, so they overlap heavily: measured across the last 8 saved
**MLB player 2.0x** slates, **87% of same-slate parlay-pairs share ≥1 leg**, and
on most slates a single leg sits in **all 5** saved parlays (e.g. 2026-07-24/-25/
-26/-29: 5 parlays but only 6–7 *distinct* legs). Smoking gun: **2026-07-26 — 5
parlays, one leg in all 5, all 5 settled LOSS.** Same shape on -25 (3 losses) and
-28 (4 losses).

Two harms:
1. **Measurement** — the paper ledger treats N overlapping parlays as N
   independent trials, so one shared-leg miss books N losses. The per-tier record
   (§15.10; player 2.0x **12-12-2 / −20% ROI**) overstates n and understates the
   effective variance — it is a correlation artifact, not N independent bad bets.
2. **Real risk** — placing all N concentrates the night on one player's outcome:
   the opposite of a diversified card.

Higher payout → longer parlays + fewer distinct qualifying legs → more forced
overlap, which is why 2.0x is hit harder than 1.4x (player 1.4x −0.5%).

## Goal

Saved builder constructions should not over-concentrate on the same underlying
outcome, so a single player (or, for team markets, a single game) can't cascade
across many saved/placed parlays. Applied uniformly across all tiers and sports
(MLB player 1.4x/2.0x, MLB team tier, NFL game tier from #3).

## Decisions (user-confirmed 2026-07-29)

- **Independence granularity: player-level.** The reuse-limited *entity* is
  `player_id` for player legs and `game_id` for team-market legs (which have no
  player). A construction's entities = the set of these across its legs.
- **Policy: a tunable per-entity exposure cap `m`, starting at `m = 2`.** Each
  entity may appear in at most `m` saved constructions. **`m = 1` is exactly
  strict-disjoint** — the later tightening, reachable by turning one knob with no
  redesign. Start at 2 (caps a single player's cascade at 2 vs today's up-to-5)
  and evaluate via the per-tier record before deciding whether to go stricter.

## Architecture — a diversification SELECTION layer

The exact two-axis search in `optimizer/builder_core.py:build()` is **unchanged**
(all its exactness guarantees stand). Only the result-assembly changes:

1. **Retain a larger ranked pool.** Today `keep()` bounds the result `heap` at
   `top_n`. Add a `pool_size` (K ≫ N; default e.g. `max(top_n * 20, 100)`) and
   bound the heap at K instead, so there is a ranked pool to diversify over. The
   search is exhaustive in ~1–2s on real slates (§15.10) and K result dicts is
   trivial memory, so widening the heap is cheap; `MAX_NODES` stays the hard
   bound. When no diversification is requested, K collapses to `top_n` (today's
   behavior exactly).

2. **Greedy diverse selection (new pure fn).**
   `select_diverse(results, n, max_uses, entity_of) -> list` walks `results` in
   **descending rank order** (the pool is already rank-sorted) and admits a
   construction only if, for every entity it uses, that entity's running count
   stays `≤ max_uses`; on admission it increments those counts. Stops at `n`
   admitted or pool exhausted. Pure, DB-free, deterministic.
   - `entity_of(leg)` = `leg["player_id"]` if `leg["kind"] == "player"` else
     `leg["game_id"]`. (Team legs have `player_id = None`.)
   - Because the search already forbids same-game legs *within* a construction, a
     construction's own entities are distinct; the cap governs reuse *across*
     selected constructions.

3. **`build()` signature.** Add `max_uses=None` (None ⇒ no cap ⇒ K = top_n ⇒
   today's exact top-N, backward-compatible) and an internal `pool_size`. When
   `max_uses` is set, build the top-K pool, then `return select_diverse(pool,
   top_n, max_uses, entity_of)`.

## Properties (honest, to be stated in UI/record where relevant)

- **Top-1 is unchanged** — the single safest construction is still the exhaustive
  global best; the cap only reshuffles slots 2…N.
- **May return fewer than N** on thin slates (not enough capped-compatible
  constructions). Same accepted pattern as the team tier being empty — not a bug.
- **Backward-compatible** — `max_uses=None` reproduces today's top-N byte-for-byte
  (the exactness oracle test must still pass unchanged).
- **Not claimed optimal** — greedy-over-rank-pool is a good de-correlated set, not
  a proven-optimal diverse set. We are removing cascade correlation, not solving a
  new optimization. No "+EV"/"edge"/"value" language is introduced (§15.8).

## Wiring

- **CLI (`optimizer/builder.py`):** add `--max-leg-reuse` (int, the cap `m`;
  default `2`) threaded into `build(max_uses=...)`. Naming: `--max-leg-reuse`
  reads naturally though the cap is per-entity (player/game); documented in help.
- **Daily chain (`scripts/daily_chain.sh`, architect's lane):** the four builder
  `--save` steps pass `--max-leg-reuse 2` **explicitly**, so the rollout is
  visible and tunable in the chain (rather than an invisible default). One-line
  edit per step.
- **API `GET /parlay-builder` (live search):** the live builder endpoint calls
  `build(...)`; pass through a `max_uses` (defaulting to the same 2) so the
  dashboard "Build" button and the saved rows are consistent. **Additive** — a new
  optional query param with a default; `/parlay-builder/saved` response **shape is
  unchanged** (only *which* constructions are saved differs, and saved rows change
  nightly anyway). Budgerr consumes rows as-is; **no contract change, FYI only, no
  coordination gate** (§7.1).

## Ordering vs NFL #3

Task C's implementation **follows the merge of NFL #3**, because both edit
`optimizer/builder_core.py` and would otherwise conflict. Task C does not depend
on #3's *content* (it keys on `leg["kind"]`/`player_id`/`game_id`, not on
`MARKET_GEOMETRY`), only on the file being settled. Spec + plan (docs) can be
written now; the worktree implementation branches after #3 lands.

## Verification

CRITICAL SAFETY: NO test DB; `ingestion.db.get_engine()` is LIVE. All tests pure
or `_FakeEngine`.

- **`select_diverse` (pure):**
  - a player capped at `m` appears in ≤ `m` selected constructions;
  - the rank-1 construction is always selected;
  - `m = 1` ⇒ no entity appears in more than one selected construction;
  - team legs key on `game_id` (a game capped at `m` appears in ≤ `m`);
  - fewer than `n` compatible ⇒ returns what it can, no crash;
  - deterministic given fixed input order.
- **`build()` regression:** `max_uses=None` returns identical results to today on
  a fixture slate (and the existing `tests/test_builder_search_exactness.py`
  oracle test must pass **unchanged** — the exact search is untouched).
- **`build()` with cap:** on a fixtured favourite-heavy slate where the top-N
  overlap, the capped result reduces max per-player reuse to `m` and still returns
  rank-descending constructions.
- **CLI:** `--max-leg-reuse` threads into `build`.
- Architect: after merge, spot-check a live `--save` run produces lower-overlap
  saved rows (re-run the 87%-overlap measurement query — expect the max
  per-player reuse to drop to `m`), kickstart `:8000` after the `api/main.py`
  change, browser-check the builder page still renders.
- Full suite stays green.

## Done criteria

1. `select_diverse` pure fn + `build(max_uses=...)` pool/selection layer; exact
   search untouched; `max_uses=None` is byte-identical to today (oracle test
   green, unchanged).
2. `--max-leg-reuse` CLI flag (default 2) + the four chain steps pass it; live
   `/parlay-builder` accepts an additive capped param.
3. Post-merge live check: saved rows' max per-player reuse ≤ `m` (was up to 5).
4. New pure tests green; full suite green.

## Out of scope

- Retroactive re-selection of already-saved rows (forward-only; the record splits
  by date so the before/after is visible).
- Correlation modeling beyond player/game identity (e.g. same-division, weather):
  YAGNI; identity-level disjointness removes the measured cascade.
- Strict-disjoint rollout (`m = 1`): available immediately via the flag; whether
  to make it the default is a later data-driven call (§15.10 record).
