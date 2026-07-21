# Builder Node-Budget Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **This is search/optimization work — dispatch to an opus-model subagent (ARCHITECT.md: modeling/statistics judgment).**

**Goal:** Make the common dashboard queries (`target_payout=1.4`, `target_payout=2.0`, `min_prob=0.75`) finish *exhaustively* instead of truncating at the 5M-node budget, by adding an exact heap-aware prune — without making the search one bit less exact.

**Architecture:** The `build()` search keeps a bounded top-N heap. Once that heap is full, any branch whose *best achievable* rank-key cannot beat the current N-th best is dead and can be pruned. This bound is exact (it discards only branches provably outside the top N) and directly attacks the node count that today's search wastes on doomed branches. A brute-force oracle plus a node-count benchmark gate every change on exactness and on real reduction.

**Tech Stack:** Pure Python (`optimizer/builder_core.py`), pytest (`tests/`). DB-free — the search is pure math; benchmarks use synthetic leg sets.

**Spec:** [`docs/superpowers/specs/2026-07-21-parlay-builder-dashboard-design.md`](../specs/2026-07-21-parlay-builder-dashboard-design.md) §7. This plan is independent of the dashboard plan — the dashboard surfaces whatever the `truncated` flag reports regardless of how far the budget is tuned here.

## Global Constraints

- **The search must stay EXACT.** No lossy heuristic, no approximate top-N, no cap that could drop the true optimum. Every change is gated by the brute-force oracle (Task 1) producing identical results. If a bound cannot be proven exact, it does not go in.
- **Rank-key semantics are unchanged.** Pin `target_payout` → rank by `joint_prob` (highest first) among constructions paying at least the floor. Pin `min_prob` → rank by `combined_odds` (highest first) among constructions at or above the probability floor. `keep()` uses strict `key > heap[0][0]`.
- **`MAX_NODES` stays a hard bound.** It is the guaranteed ceiling against the OOM this builder exists to prevent (README §11/§15). Tightening pruning reduces how often it is *hit*; it never removes it.
- **Prefer exact pruning over raising `MAX_NODES`.** A raised budget trades latency against the 4–13s response time the dashboard already works around. Only raise the budget in Task 3, only after pruning has done its work, and only with the measured latency in hand.
- **DB-free and `env -i`-clean.** All tests run under `env -i PATH=/usr/bin:/bin HOME=$HOME /Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest`.

## Reference — current `build()` structure (`optimizer/builder_core.py`, on main `1f00735`)

`build()` groups legs `by_game`, sorts games by cheapest leg and legs within a game price-ascending, precomputes `best_from[gi][r]` = the largest payout obtainable from `r` legs drawn from `games[gi:]` (an exact suffix-max used today only for the payout-floor *lower* bound). It then runs, per widening ceiling, a `descend()` DFS feeding a bounded top-N `heap` via `keep()`. `rank_by_payout = target_payout is None and min_prob is not None`. `keep()` sets `key = combined_odds if rank_by_payout else joint_prob`, pushes while `len(heap) < top_n`, else replaces the min if `key > heap[0][0]`. `state = {"nodes", "truncated", "matches"}`; `stats` also gets `candidate_games`.

**The gap:** `descend()` prunes on the payout floor (`best_from` lower bound) and the ceiling (`next_odds > hi`), and on the probability floor (`next_prob < min_prob`), but it does **not** prune against the top-N heap. A branch is explored even when it provably cannot enter the current top N. That is the wasted work.

---

### Task 1: Brute-force exactness oracle + node-count benchmark

**Files:**
- Test: `tests/test_builder_search_exactness.py` (create)

**Interfaces:**
- Produces: `brute_force_build(legs, target_payout, min_prob, min_legs, max_legs, top_n)` — a naive-but-obviously-correct reference returning results in the same rank order as `build()`; and `search_key_seq(results, rank_by_payout)` — a comparison helper reducing a result list to a comparable sequence. Used by Tasks 2 and 3.

- [ ] **Step 1: Write the oracle and a slate generator, and assert the oracle matches current `build()`**

The oracle enumerates every across-game combination directly (small slates only), so its correctness is self-evident. Use distinct keys so the top-N boundary is never a tie (keeps the comparison unambiguous).

```python
# tests/test_builder_search_exactness.py
import itertools
import random
from optimizer import builder_core


def _leg(game_id, decimal_odds, market_prob, i):
    return {
        "game_id": game_id, "kind": "player", "label": f"L{i}",
        "side": "under", "decimal_odds": decimal_odds,
        "american_odds": -100, "market_prob": market_prob,
        "model_prob": None, "line_value": 0.5, "player_id": i,
        "stat_type": "hits", "market": None,
    }


def brute_force_build(legs, target_payout=None, min_prob=None,
                      min_legs=2, max_legs=4, top_n=10):
    rank_by_payout = target_payout is None and min_prob is not None
    results = []
    for size in range(min_legs, max_legs + 1):
        for combo in itertools.combinations(legs, size):
            games = [l["game_id"] for l in combo]
            if len(set(games)) != len(games):
                continue  # same-game excluded
            odds = 1.0
            prob = 1.0
            for l in combo:
                odds *= l["decimal_odds"]
                prob *= l["market_prob"]
            if target_payout is not None and odds < target_payout:
                continue
            if min_prob is not None and prob < min_prob:
                continue
            results.append({"combined_odds": odds, "joint_prob": prob, "n_legs": size})
    key = (lambda r: r["combined_odds"]) if rank_by_payout else (lambda r: r["joint_prob"])
    results.sort(key=key, reverse=True)
    return results[:top_n]


def search_key_seq(results, rank_by_payout):
    key = "combined_odds" if rank_by_payout else "joint_prob"
    return [round(r[key], 12) for r in results]


def _random_slate(rng, n_games, legs_per_game):
    legs, i = [], 0
    for g in range(n_games):
        for _ in range(legs_per_game):
            # distinct odds/probs keep the top-N boundary tie-free
            odds = round(1.0 + rng.random() * 1.5 + i * 1e-4, 6)
            prob = round(min(0.99, 0.55 + rng.random() * 0.44 - i * 1e-5), 6)
            legs.append(_leg(g, odds, prob, i))
            i += 1
    return legs


def test_oracle_matches_current_build_across_params():
    rng = random.Random(7)
    for _ in range(40):
        legs = _random_slate(rng, n_games=rng.randint(3, 6), legs_per_game=rng.randint(1, 4))
        for params in (
            {"target_payout": 2.0}, {"target_payout": 1.4}, {"target_payout": 3.0},
            {"min_prob": 0.75}, {"min_prob": 0.5}, {"min_prob": 0.9},
        ):
            top_n = rng.choice([1, 3, 10])
            rank_by_payout = params.get("target_payout") is None
            got = builder_core.build(legs, top_n=top_n, **params)
            exp = brute_force_build(legs, top_n=top_n, **params)
            assert search_key_seq(got, rank_by_payout) == search_key_seq(exp, rank_by_payout), params
```

- [ ] **Step 2: Run it — the oracle must already match the (un-modified) build()**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder_search_exactness.py -v`
Expected: PASS. (This validates the oracle against today's search *before* you change anything. If it fails, the oracle is wrong — fix the oracle, not `build()`.)

- [ ] **Step 3: Add a node-count benchmark that captures the baseline**

Append a test that builds a larger synthetic slate resembling the live shape (~19 games, ~128 legs/game = ~2400 legs) and records node counts via the `stats` dict, asserting the *current* behaviour so a regression is visible. This is the before-picture Task 2 must improve.

```python
def _big_slate():
    rng = random.Random(1)
    return _random_slate(rng, n_games=19, legs_per_game=128)


def test_baseline_node_counts_are_captured():
    legs = _big_slate()
    for params in ({"target_payout": 1.4}, {"target_payout": 2.0}, {"min_prob": 0.75}):
        stats = {}
        builder_core.build(legs, top_n=5, stats=stats, **params)
        # Baseline (main 1f00735): all three saturate the 5M budget.
        # Task 2 must bring at least the 2-4 leg common queries under it.
        print(params, "nodes=", stats["nodes"], "truncated=", stats["truncated"])
        assert stats["nodes"] <= builder_core.MAX_NODES + 1
```

Run it with `-s` and record the printed baseline node counts in the commit message.

- [ ] **Step 4: Commit**

```bash
git add tests/test_builder_search_exactness.py
git commit -m "test(builder): brute-force exactness oracle + node-count benchmark

Oracle enumerates across-game combos directly and matches the current
search across randomized slates and both rank modes; benchmark captures
the baseline node counts (all three canonical queries saturate the 5M
budget on a ~2400-leg slate) so the pruning work has a before-picture.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Exact heap-aware top-N pruning

**Files:**
- Modify: `optimizer/builder_core.py` (the `descend()` recursion inside `build()`)
- Test: `tests/test_builder_search_exactness.py` (extend)

**Interfaces:**
- Consumes: `brute_force_build`, `search_key_seq` (Task 1).
- Produces: no signature change to `build()`. Same results, fewer nodes.

**The bound (why it is exact):**
- **Rank-by-payout path** (`min_prob` pinned): a partial branch with running `odds` and `remaining = size - len(chosen)` legs still to pick can reach at most `odds * best_from[gi][remaining]` (the exact suffix-max already computed). If the heap is full (`len(heap) == top_n`) and that maximum is `<= heap[0][0]`, no completion of this branch can strictly beat the current N-th best, so the branch is dead.
- **Rank-by-joint_prob path** (`target_payout` pinned): decimal odds are all `>= 1` and market probs are all `<= 1`, so `joint_prob` only *falls* as legs are added — the running `prob` is itself an upper bound on any completion's `joint_prob`. If the heap is full and `prob <= heap[0][0]`, no completion can strictly beat the N-th best. (This one needs no suffix structure — the monotonic decrease is the bound.)

Both discard only branches that provably cannot enter the top N, so the returned top N is unchanged.

- [ ] **Step 1: Write the failing test (exactness under pruning + a strict node reduction)**

```python
def test_pruning_preserves_exactness_and_cuts_nodes():
    # Exactness: identical to the oracle across the same randomized sweep.
    rng = random.Random(11)
    for _ in range(40):
        legs = _random_slate(rng, n_games=rng.randint(3, 6), legs_per_game=rng.randint(1, 4))
        for params in ({"target_payout": 2.0}, {"target_payout": 1.4},
                       {"min_prob": 0.75}, {"min_prob": 0.5}):
            top_n = rng.choice([1, 3, 10])
            rank_by_payout = params.get("target_payout") is None
            got = builder_core.build(legs, top_n=top_n, **params)
            exp = brute_force_build(legs, top_n=top_n, **params)
            assert search_key_seq(got, rank_by_payout) == search_key_seq(exp, rank_by_payout), params

    # Reduction: on the big slate, the common queries no longer saturate.
    legs = _big_slate()
    for params in ({"target_payout": 2.0}, {"min_prob": 0.75}):
        stats = {}
        builder_core.build(legs, top_n=5, stats=stats, **params)
        assert stats["truncated"] is False, (params, stats["nodes"])
```

- [ ] **Step 2: Run it to confirm the reduction assertion fails today**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder_search_exactness.py::test_pruning_preserves_exactness_and_cuts_nodes -v`
Expected: FAIL on the `truncated is False` assertion (today it truncates). The exactness half should already pass.

- [ ] **Step 3: Add the prune to `descend()`**

Inside `build()`'s `descend(start, chosen, odds, prob, size)`, in the loop over games `for gi in range(start, n_games - remaining + 1):`, add the heap-aware prune alongside the existing `best_from` floor prune. Immediately after the existing:

```python
            if lo is not None and odds * best_from[gi][remaining] < lo:
                break
```

insert:

```python
            # Heap-aware prune (exact): once we hold top_n, drop any branch whose
            # best achievable rank-key cannot strictly beat the current N-th best.
            # Rank-by-payout: max completion payout is odds * best_from[gi][remaining]
            # (exact suffix maximum). Rank-by-joint_prob: joint_prob only falls as
            # legs are added, so the running prob is itself the upper bound. Both
            # discard only branches provably outside the top N — results unchanged.
            if len(heap) == top_n:
                if rank_by_payout:
                    if odds * best_from[gi][remaining] <= heap[0][0]:
                        break  # games are odds-ascending in suffix-max terms → later gi only worse
                else:
                    if prob <= heap[0][0]:
                        return  # prob doesn't depend on gi; no later game helps
```

Note the asymmetry: for the payout path the bound depends on `best_from[gi][...]`, which only shrinks as `gi` advances, so `break` (abandon the whole game loop) is valid — mirroring the existing floor prune. For the joint-prob path the bound (`prob`) is independent of `gi`, so once it fails, no game at this level can qualify: `return`. Confirm `heap`, `top_n`, `rank_by_payout`, `best_from` are all in scope inside the per-ceiling closure (they are — `descend`/`keep` are redefined each ceiling pass).

- [ ] **Step 4: Run the exactness+reduction test**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder_search_exactness.py -v`
Expected: PASS — exactness holds and both common queries now finish without truncating.

- [ ] **Step 5: Full suite green (no regression in the existing builder tests)**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest -q`
Expected: all pass, including the existing `tests/test_builder_core.py` exactness/pruning cases.

- [ ] **Step 6: Re-measure and record**

Run the baseline benchmark again with `-s` and record the new node counts for all three canonical queries (`target_payout=1.4`, `2.0`, `min_prob=0.75`) in the commit message, next to the Task 1 baseline, so the reduction is documented.

- [ ] **Step 7: Commit**

```bash
git add optimizer/builder_core.py tests/test_builder_search_exactness.py
git commit -m "perf(builder): exact heap-aware top-N pruning

Once the bounded top-N heap is full, a branch whose best achievable rank-
key cannot strictly beat the current N-th best is dead. Payout path bounds
the completion with the existing best_from suffix maximum; joint-prob path
uses the running probability, which only falls as legs are added. Both
discard only provably-out-of-top-N branches, so results are identical
(verified against the brute-force oracle) — but <BASELINE> nodes drop to
<NEW> and the 1.4x/2.0x/0.75 dashboard queries no longer truncate.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Tune `MAX_NODES` against measured latency and document

Only if a canonical query *still* truncates after Task 2. If all three finish exhaustively, this task is a documentation-only pass (skip the tuning, do Steps 4–5).

**Files:**
- Modify: `optimizer/builder_core.py` (the `MAX_NODES` constant + its comment, only if changed)
- Modify: `README.md` §15.10 (correct the truncation claim)

- [ ] **Step 1: Measure wall time for the canonical queries on the big slate**

Time `build()` for `target_payout=1.4`, `2.0`, `min_prob=0.75` on `_big_slate()` (use `time.perf_counter` around the call, `top_n=5`). Record wall time and node count for each.

- [ ] **Step 2: Decide the budget**

If any query still truncates: raise `MAX_NODES` only as far as needed for the common queries to finish, and only if the resulting wall time stays within a few seconds (the dashboard fires an explicit Build with a pending state, but a >~5s search is poor UX). If finishing exhaustively would cost more than that, **leave `MAX_NODES` where it is** — the dashboard surfaces the `truncated` flag honestly, and a truncated 2-leg-exhaustive result is an acceptable, disclosed outcome (spec §7). Record the decision and its reasoning.

- [ ] **Step 3: If you changed `MAX_NODES`, update its comment**

Keep the OOM-history note; add the measured justification for the new value (which queries it lets finish, at what wall time).

- [ ] **Step 4: Correct README §15.10**

The "Known limitations" bullet currently says the 2.0x search truncates and implies the 1.4x is fine. Replace it with the measured post-pruning reality: which of the three canonical queries now finish exhaustively and which (if any) still truncate, with the node counts. Note that heap-aware pruning was added and that truncation, where it remains, is surfaced in the UI, not hidden.

- [ ] **Step 5: Commit**

```bash
git add optimizer/builder_core.py README.md
git commit -m "perf(builder): tune node budget + record post-pruning truncation reality

<one line on whether MAX_NODES changed and the measured wall times>. README
§15.10 corrected: <which canonical queries finish exhaustively now>.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage (§7):** "tighten pruning before raising MAX_NODES" → Task 2 (pruning) precedes Task 3 (budget). "existing suffix-maximum bound … are exact; look for further exact bounds" → Task 2's bound reuses `best_from` and the monotonic prob decrease, both exact, gated by the Task 1 oracle. "search is exact and must stay exact" → every change gated by `brute_force_build`. "report node count, wall time, truncation for the three queries" → Task 2 Step 6 (nodes) + Task 3 Step 1 (wall time) + README §15.10 (Task 3 Step 4). "still truncating is acceptable if surfaced, not hidden by raising the budget" → Task 3 Step 2 makes leaving `MAX_NODES` the default when exhaustive is too costly.

**Placeholder scan:** `<BASELINE>`/`<NEW>` in the Task 2 commit message and the `<...>` in Task 3's are fill-in-the-measured-number slots, explicitly instructed to be filled from the `-s` output in the preceding step — not vague code placeholders. All code steps carry complete code.

**Type consistency:** `brute_force_build` and `search_key_seq` signatures match between Task 1 (definition) and Tasks 2 (use). `_leg`, `_random_slate`, `_big_slate` defined in Task 1, reused in Task 2. The prune reads `heap`, `top_n`, `rank_by_payout`, `best_from`, `remaining`, `odds`, `prob`, `gi` — all in scope in `descend()` per the current structure.
