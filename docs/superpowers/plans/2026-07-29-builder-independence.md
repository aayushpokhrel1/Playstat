# Builder Independence (diverse top-N) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop saved builder parlays from over-concentrating on the same player/game, so one outcome can't cascade across many saved cards.

**Architecture:** A pure `select_diverse()` selection layer runs over a widened top-K result pool in `optimizer/builder_core.py:build()`; the exact two-axis search is untouched. A tunable per-entity exposure cap `m` (player_id for player legs, game_id for team legs; `m=1` = strict disjoint) governs reuse. Threaded through the CLI (`--max-leg-reuse`, default 2), the four daily-chain builder steps, and the live `GET /parlay-builder` endpoint.

**Tech Stack:** Python 3.11, pytest. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-07-29-builder-independence-design.md](../specs/2026-07-29-builder-independence-design.md)

## Global Constraints

- **SEQUENCING: implement only AFTER NFL #3 has merged to `main`.** Both edit `optimizer/builder_core.py`; branch this worktree from a `main` that already contains #3. Task C does not depend on #3's content (it keys on `leg["kind"]`/`player_id`/`game_id`), only on the file being settled.
- **§15.8 guardrails:** no "+EV"/"edge"/"value"/"beat the market" language anywhere; no signal-green; the change introduces no new claims.
- **Backward compatibility is a hard requirement:** `build(max_uses=None)` MUST return byte-identical results to today. `tests/test_builder_search_exactness.py` (the brute-force oracle) MUST pass **unchanged** — the exact search is not modified.
- **NO TEST DB. `ingestion.db.get_engine()` is LIVE.** All tests pure or `_FakeEngine` (see `tests/test_parlay_builder_api.py` for the endpoint fake-engine pattern — note it monkeypatches `main.engine`). A test that hits a real socket is a defect.
- **Budgerr (§7.1):** `/parlay-builder/saved` response shape unchanged; the live `/parlay-builder` gains an ADDITIVE optional query param only. Do not change `/parlay-builder/saved`.
- **Worktree note:** `graphify-out/` gitignored/absent — read source directly. Interpreter `/Users/aayushpokhrel/dev/playstat/.venv/bin/python` from worktree cwd. `.env` not needed (tests pure/fake-engine).
- Suite run: `.venv/bin/python -m pytest -q`. Baseline = whatever #3 left green (≥ 299 + #3's additions).

---

## Task 1: `select_diverse` pure selection layer + `build(max_uses=...)`

**Files:**
- Modify: `optimizer/builder_core.py` (`build` signature ~L137-139, the `keep()` heap bound ~L237-246, the final result assembly/return)
- Test: `tests/test_builder_core.py` (extend)

**Interfaces:**
- Produces: `entity_of(leg: dict) -> int` (player_id for player legs, game_id for team legs); `select_diverse(results: list[dict], n: int, max_uses: int, entity_of=entity_of) -> list[dict]`; `build(..., max_uses: int | None = None)`.

- [ ] **Step 1: Write failing tests for `entity_of` + `select_diverse`** (`tests/test_builder_core.py`)

```python
from optimizer.builder_core import entity_of, select_diverse

def _con(legs, jp):
    return {"legs": legs, "joint_prob": jp, "combined_odds": 1.0 + jp}

def _pl(pid, gid): return {"kind": "player", "player_id": pid, "game_id": gid}
def _tm(gid):      return {"kind": "team", "player_id": None, "game_id": gid}

def test_entity_of_player_vs_team():
    assert entity_of(_pl(500, 9)) == 500
    assert entity_of(_tm(9)) == 9

def test_select_diverse_caps_player_reuse():
    # player 1 in every construction; cap m=2 -> at most 2 selected use player 1
    results = [_con([_pl(1, g), _pl(2 + i, g + 100)], 0.9 - i * 0.01)
               for i, g in enumerate([10, 20, 30, 40, 50])]
    out = select_diverse(results, n=5, max_uses=2)
    used = sum(1 for c in out if any(entity_of(l) == 1 for l in c["legs"]))
    assert used == 2
    assert out[0] is results[0]         # rank-1 always kept

def test_select_diverse_m1_is_strict_disjoint():
    results = [_con([_pl(1, 10), _pl(2, 20)], 0.9),
               _con([_pl(1, 30), _pl(3, 40)], 0.8),   # reuses player 1 -> excluded
               _con([_pl(4, 50), _pl(5, 60)], 0.7)]
    out = select_diverse(results, n=5, max_uses=1)
    assert [c["joint_prob"] for c in out] == [0.9, 0.7]

def test_select_diverse_team_legs_key_on_game():
    results = [_con([_tm(10)], 0.9), _con([_tm(10)], 0.8), _con([_tm(11)], 0.7)]
    out = select_diverse(results, n=5, max_uses=1)
    assert [c["joint_prob"] for c in out] == [0.9, 0.7]

def test_select_diverse_returns_fewer_than_n_gracefully():
    results = [_con([_pl(1, 10)], 0.9), _con([_pl(1, 20)], 0.8)]
    assert len(select_diverse(results, n=5, max_uses=1)) == 1
```

- [ ] **Step 2: Run, verify they fail.**

- [ ] **Step 3: Implement `entity_of` + `select_diverse`** (`optimizer/builder_core.py`, module level near the other pure helpers)

```python
def entity_of(leg):
    """Reuse-limited identity: the player for a player leg, else the game."""
    return leg["player_id"] if leg["kind"] == "player" else leg["game_id"]

def select_diverse(results, n, max_uses, entity_of=entity_of):
    """Greedily pick up to n constructions in the given (rank-descending) order,
    admitting one only if every entity it uses stays within max_uses. Not claimed
    optimal — it removes cross-construction reuse cascade, nothing more."""
    counts, chosen = {}, []
    for con in results:
        ents = {entity_of(l) for l in con["legs"]}
        if all(counts.get(e, 0) < max_uses for e in ents):
            for e in ents:
                counts[e] = counts.get(e, 0) + 1
            chosen.append(con)
            if len(chosen) == n:
                break
    return chosen
```

- [ ] **Step 4: Run, verify they pass.**

- [ ] **Step 5: Write failing tests for `build(max_uses=...)`** (`tests/test_builder_core.py`) — one regression (`max_uses=None` unchanged) and one capped:

```python
from optimizer.builder_core import build

def _leg(gid, pid, prob, odds):
    return {"game_id": gid, "player_id": pid, "kind": "player", "stat_type": "x",
            "side": "over", "line_value": 0.5, "american_odds": odds,
            "decimal_odds": 1 + prob, "market_prob": prob, "model_prob": None, "market": None}

def test_build_max_uses_none_matches_today():
    legs = [_leg(g, g, 0.8, -150) for g in range(1, 6)]
    a = build(legs, target_payout=1.4, top_n=5)
    b = build(legs, target_payout=1.4, top_n=5, max_uses=None)
    assert [c["joint_prob"] for c in a] == [c["joint_prob"] for c in b]

def test_build_cap_reduces_player_reuse():
    # a dominant favourite (player 99) appears in many top constructions
    legs = [_leg(1, 99, 0.95, -400)] + [_leg(g, g, 0.75, -120) for g in range(2, 8)]
    capped = build(legs, target_payout=1.4, top_n=5, max_uses=2)
    uses99 = sum(1 for c in capped for l in c["legs"] if l["player_id"] == 99)
    assert uses99 <= 2
```

- [ ] **Step 6: Run, verify they fail** (build has no `max_uses` kwarg yet).

- [ ] **Step 7: Add `max_uses` to `build`, widen the pool, select at the end.**

In `build`'s signature add `max_uses=None`. Compute the pool bound and bound the heap by it (replace the `top_n` references inside `keep()` with `pool_n`):

```python
    pool_n = top_n if max_uses is None else max(top_n * 20, 100)
```

Change `keep()` to use `pool_n` instead of `top_n` for the heap capacity (`if len(heap) < pool_n:` / `elif key > heap[0][0]:`). Leave every prune, bound, and node-count exactly as-is (they do not reference `top_n` for correctness — verify by reading; the prunes compare against `heap[0][0]`, the pool's current worst, which is correct for any pool size).

At the final assembly (where the heap is drained/sorted into the returned list — the existing rank-descending sort), apply the selection when capped:

```python
    ranked = [entry[2] for entry in sorted(heap, reverse=True)]   # existing rank-descending drain
    if max_uses is None:
        return ranked[:top_n]
    return select_diverse(ranked, top_n, max_uses)
```

> If the existing drain already slices `[:top_n]`, replace that slice with the block above so the full pool reaches `select_diverse`. Read the current return carefully and preserve its sort key (combined_odds when `rank_by_payout`, else joint_prob — `select_diverse` is order-preserving so the sort must already be correct).

- [ ] **Step 8: Run the new build tests + the exactness oracle** — `.venv/bin/python -m pytest tests/test_builder_core.py tests/test_builder_search_exactness.py -q`. The oracle MUST pass unchanged.

- [ ] **Step 9: Run the full suite** → green.

- [ ] **Step 10: Commit** — `git commit -am "feat(builder): diverse top-N selection layer + per-entity exposure cap (build max_uses)"`

---

## Task 2: CLI flag + live endpoint + daily chain

**Files:**
- Modify: `optimizer/builder.py` (`main` argparse + the `build(...)` call ~L212)
- Modify: `api/main.py` (the live `GET /parlay-builder` handler — add an additive query param, pass to `build`)
- Modify: `scripts/daily_chain.sh` (four builder `--save` steps — architect's lane; done by the architect, listed here for completeness)
- Test: `tests/test_builder.py` and/or `tests/test_parlay_builder_api.py` (extend)

**Interfaces:**
- Consumes: `build(..., max_uses=...)` from Task 1.
- Produces: `optimizer.builder --max-leg-reuse M`; `GET /parlay-builder?...&max_leg_reuse=M`.

- [ ] **Step 1: Write a failing test that the CLI threads `--max-leg-reuse` into `build`** (`tests/test_builder.py`) — monkeypatch `optimizer.builder.build` to capture kwargs, invoke `main()` with argv including `--max-leg-reuse 3` and a stubbed engine/loaders (follow the existing `main`-invocation test pattern in the file; if none exists, test the argparse default instead):

```python
def test_cli_max_leg_reuse_threads_into_build(monkeypatch):
    captured = {}
    monkeypatch.setattr("optimizer.builder.build",
                        lambda *a, **k: captured.update(k) or [])
    monkeypatch.setattr("optimizer.builder.db.get_engine", lambda: object())
    monkeypatch.setattr("optimizer.builder.load_legs", lambda *a, **k: [{"x": 1}])
    monkeypatch.setattr("sys.argv",
                        ["builder", "--target-payout", "1.4", "--max-leg-reuse", "3"])
    from optimizer.builder import main
    main()
    assert captured["max_uses"] == 3
```

- [ ] **Step 2: Run, verify it fails.**

- [ ] **Step 3: Add the argparse flag + thread it** (`optimizer/builder.py`)

```python
    parser.add_argument("--max-leg-reuse", type=int, default=2,
                        help="cap how many saved constructions may reuse the same "
                             "player (or, for team markets, the same game). 1 = fully "
                             "disjoint. Diversifies the top-N so one outcome can't "
                             "cascade across many cards.")
```

In the `build(...)` call add `max_uses=args.max_leg_reuse`.

- [ ] **Step 4: Run the CLI test + full suite** → green.

- [ ] **Step 5: Write a failing test for the endpoint param** (`tests/test_parlay_builder_api.py`) — assert `GET /parlay-builder?...&max_leg_reuse=1` passes `max_uses=1` into `build` (monkeypatch `main.build`, use the file's existing fake-engine + `main.engine` monkeypatch so it never hits the live DB).

- [ ] **Step 6: Run, verify it fails.**

- [ ] **Step 7: Add the additive query param to `GET /parlay-builder`** (`api/main.py`) — `max_leg_reuse: int = 2`, passed as `max_uses=max_leg_reuse` into the `build(...)` call. Do not touch `/parlay-builder/saved`.

- [ ] **Step 8: Run the endpoint test + full suite** → green.

- [ ] **Step 9: Commit** — `git commit -am "feat(builder): --max-leg-reuse CLI + additive /parlay-builder max_leg_reuse param"`

- [ ] **Step 10 (ARCHITECT, not the agent): daily chain + kickstart.** The architect edits the four builder `--save` steps in `scripts/daily_chain.sh` to append `--max-leg-reuse 2`, `bash -n` checks it, and runs `launchctl kickstart -k gui/$(id -u)/com.playstat.api` (the `api/main.py` change needs the live API restarted). Then re-runs the 87%-overlap measurement query on the next slate's saved rows to confirm max per-player reuse dropped to ≤ 2.

---

## Self-review (completed by plan author)

- **Spec coverage:** selection layer + cap → Task 1; CLI/endpoint/chain wiring → Task 2; backward-compat (`max_uses=None` + oracle unchanged) → Task 1 Step 8; player/game entity → `entity_of` Task 1; Budgerr additive → Task 2 Step 7. All mapped.
- **Type consistency:** `entity_of`/`select_diverse`/`max_uses` names identical across defs, tests, and call sites; `pool_n` internal only.
- **No live-DB from agent:** all tests pure or fake-engine (`main.engine` monkeypatched); chain edit + kickstart are the architect's Step 10.
- **Sequencing guard:** Global Constraints pin implementation after #3 merges.

## Execution handoff

Architect executes AFTER NFL #3 merges: dispatch a worktree subagent for Tasks 1–2 (review diffs between), then perform the chain edit + kickstart (Task 2 Step 10) and the live overlap re-measurement, then merge.
