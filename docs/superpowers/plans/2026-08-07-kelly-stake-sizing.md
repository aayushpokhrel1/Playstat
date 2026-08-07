# Kelly Stake Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat 1u paper stake with a ¼-Kelly stake sized on the line-shopping edge, plus a configurable same-night total-exposure cap.

**Architecture:** Pure math in a new `optimizer/stake.py` (Kelly fraction, ¼-Kelly stake, exposure cap) + a slate-sizing pass that runs once after all builder steps and writes a new additive `stake` column on `parlay_recommendations`. `settle` reads that column (NULL→1.0 fallback) and threads it through the already-stake-aware `parlay_result`. Two dashboard-only record shapers switch ROI from `pnl/n` to `pnl/staked`. Additive-only; no Budgerr shape change; settle's payout math untouched.

**Tech Stack:** Python 3.11, SQLAlchemy Core (`text()`), PostgreSQL (live — no test DB), pytest.

## Global Constraints

- **Additive-only.** No column drops, no response-shape changes to Budgerr surfaces (`/box-scores`, `/games`, `/parlay-builder/saved`). New DB column is nullable.
- **No test DB.** `ingestion.db.get_engine()` is LIVE. New tests are pure (no DB) or use the fake-engine pattern (`tests/test_builder.py:_CapturingEngine`, `tests/test_parlay_builder_api.py`). Tests must run under `env -i` (no env deps).
- **Guardrail §15.8 (binding).** Rank only on devig `market_prob`; `market_prob ≥ 0.55`; 2–4 legs; across-game only; paper-only. **UI/API/JSONB copy: NO "+EV"/"edge"/"value"/"beat the market" language, no signal-green.** Frame strictly as "stake sizing".
- **Edge definition (approved):** `p = joint_prob` (consensus devig), `d = combined_odds` (shopped), `f* = (p·d − 1)/(d − 1)` clamped ≥ 0.
- **Unit convention:** 1u = 1% bankroll → `stake_units = 0.25 · f* · 100`. Defaults: `fraction=0.25`, `bankroll_units=100`.
- **Exposure cap defaults:** `5.0` units, `global` per-date scope; both configurable (`--exposure-cap`, `--cap-scope global|per-sport`).
- **`graphify query "<question>"` before reading/grepping source** (graph is in the main checkout only; in a worktree read source directly). Run `graphify update .` after code changes.
- **Reserved to the architect (NOT delegated):** applying migration `010` to the live DB (back up schema first), the `git push`, and wiring the chain step. Workers commit in their worktree only.

---

### Task 1: Pure Kelly math — `optimizer/stake.py`

**Files:**
- Create: `optimizer/stake.py`
- Test: `tests/test_stake.py`

**Interfaces:**
- Produces:
  - `kelly_fraction(p: float, decimal_odds: float) -> float`
  - `quarter_kelly_stake(p: float, decimal_odds: float, *, fraction: float = 0.25, bankroll_units: float = 100) -> float`
  - `apply_exposure_cap(stakes: list[float], cap: float) -> list[float]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stake.py
import pytest
from optimizer.stake import kelly_fraction, quarter_kelly_stake, apply_exposure_cap


def test_kelly_zero_when_fair_priced():
    # p == 1/d  ->  p*d == 1  ->  no edge  ->  f* == 0
    assert kelly_fraction(0.5, 2.0) == pytest.approx(0.0)


def test_kelly_positive_with_shopped_uplift():
    # p=0.50, d=2.04  ->  edge = 0.02  ->  f* = 0.02/1.04
    assert kelly_fraction(0.50, 2.04) == pytest.approx(0.02 / 1.04, rel=1e-9)


def test_kelly_clamped_to_zero_when_negative():
    # p=0.68, d=1.40  ->  p*d = 0.952 < 1  ->  clamp to 0
    assert kelly_fraction(0.68, 1.40) == 0.0


def test_kelly_zero_when_decimal_odds_not_above_one():
    assert kelly_fraction(0.9, 1.0) == 0.0
    assert kelly_fraction(0.9, 0.5) == 0.0


def test_quarter_kelly_unit_scaling():
    # 4% edge on a 2.0x parlay -> f*=0.04 -> 0.25*0.04*100 = 1.0u
    assert quarter_kelly_stake(0.52, 2.0) == pytest.approx(0.25 * 0.04 * 100, rel=1e-9)
    # 2% edge -> ~0.5u
    assert quarter_kelly_stake(0.51, 2.0) == pytest.approx(0.5, rel=1e-9)


def test_quarter_kelly_zero_when_no_edge():
    assert quarter_kelly_stake(0.5, 2.0) == 0.0


def test_exposure_cap_noop_under_cap():
    assert apply_exposure_cap([0.3, 0.4, 0.5], 5.0) == [0.3, 0.4, 0.5]


def test_exposure_cap_scales_proportionally_when_over():
    out = apply_exposure_cap([4.0, 4.0], 5.0)
    assert sum(out) == pytest.approx(5.0)
    assert out[0] == out[1] == pytest.approx(2.5)


def test_exposure_cap_all_zero_unchanged():
    assert apply_exposure_cap([0.0, 0.0], 5.0) == [0.0, 0.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -i .venv/bin/python -m pytest tests/test_stake.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimizer.stake'`

- [ ] **Step 3: Write the implementation**

```python
# optimizer/stake.py
"""Kelly stake sizing for builder parlays (README §15.9 item 4).

PURE math + a slate-sizing pass. The edge sized on is the line-shopping edge:
p = consensus devig joint_prob, d = shopped combined_odds. There is NO +EV/edge
claim in any UI — this is stake sizing only.
"""


def kelly_fraction(p, decimal_odds):
    """Kelly fraction of bankroll for a single bet with win prob p and net
    decimal odds (decimal_odds - 1). f* = (p*d - 1)/(d - 1), clamped to >= 0
    (never stake into a non-positive edge). Returns 0 when decimal_odds <= 1.
    """
    if decimal_odds <= 1:
        return 0.0
    f = (p * decimal_odds - 1) / (decimal_odds - 1)
    return f if f > 0 else 0.0


def quarter_kelly_stake(p, decimal_odds, *, fraction=0.25, bankroll_units=100):
    """Fractional-Kelly stake in UNITS, where 1 unit = 1% of bankroll
    (bankroll_units = 100). stake = fraction * f* * bankroll_units.
    """
    return fraction * kelly_fraction(p, decimal_odds) * bankroll_units


def apply_exposure_cap(stakes, cap):
    """Scale a group of stakes down proportionally so their sum never exceeds
    cap. No-op when already under the cap or all-zero. Pure list-in/list-out.
    """
    total = sum(stakes)
    if total <= cap or total <= 0:
        return list(stakes)
    scale = cap / total
    return [s * scale for s in stakes]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env -i .venv/bin/python -m pytest tests/test_stake.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add optimizer/stake.py tests/test_stake.py
git commit -m "feat(stake): pure Kelly fraction + quarter-Kelly + exposure cap (§15.9 item 4)"
```

---

### Task 2: Slate-sizing helper (pure) — `optimizer/stake.py:size_slate`

**Files:**
- Modify: `optimizer/stake.py` (append `size_slate`)
- Test: `tests/test_stake.py` (append)

**Interfaces:**
- Consumes: `quarter_kelly_stake`, `apply_exposure_cap` (Task 1).
- Produces: `size_slate(rows, *, exposure_cap=5.0, cap_scope="global", fraction=0.25, bankroll_units=100) -> dict[int, float]` where `rows` is a list of `(parlay_id: int, p: float, decimal_odds: float, sport: str)` and the return maps `parlay_id -> stake`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_stake.py
from optimizer.stake import size_slate


def test_size_slate_global_cap_groups_all_sports_together():
    # two big raw stakes across different sports, global cap 5u -> summed & scaled
    rows = [(1, 0.52, 2.0, "mlb"), (2, 0.52, 2.0, "nfl")]  # each raw ~1.0u
    out = size_slate(rows, exposure_cap=1.0, cap_scope="global")
    assert sum(out.values()) == pytest.approx(1.0)
    assert out[1] == out[2] == pytest.approx(0.5)


def test_size_slate_per_sport_cap_is_independent():
    rows = [(1, 0.52, 2.0, "mlb"), (2, 0.52, 2.0, "nfl")]  # each raw ~1.0u
    out = size_slate(rows, exposure_cap=1.0, cap_scope="per-sport")
    # each sport has its own 1u budget -> neither is scaled
    assert out[1] == pytest.approx(1.0)
    assert out[2] == pytest.approx(1.0)


def test_size_slate_zero_edge_cards_get_zero():
    rows = [(1, 0.5, 2.0, "mlb"), (2, 0.52, 2.0, "mlb")]  # card 1 no edge, card 2 ~1u
    out = size_slate(rows, exposure_cap=5.0)
    assert out[1] == 0.0
    assert out[2] == pytest.approx(1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -i .venv/bin/python -m pytest tests/test_stake.py -k size_slate -v`
Expected: FAIL — `ImportError: cannot import name 'size_slate'`

- [ ] **Step 3: Write the implementation**

```python
# append to optimizer/stake.py
def size_slate(rows, *, exposure_cap=5.0, cap_scope="global", fraction=0.25, bankroll_units=100):
    """Size a whole night's builder parlays. rows: (parlay_id, p, decimal_odds,
    sport). Computes the per-parlay quarter-Kelly stake, then applies the
    exposure cap within each cap group ('global' = one group for the date;
    'per-sport' = one group per sport). Returns {parlay_id: stake}. Pure.
    """
    raw = {pid: quarter_kelly_stake(p, d, fraction=fraction, bankroll_units=bankroll_units)
           for pid, p, d, _sport in rows}
    groups = {}
    for pid, _p, _d, sport in rows:
        key = "all" if cap_scope == "global" else (sport or "mlb")
        groups.setdefault(key, []).append(pid)
    out = {}
    for pids in groups.values():
        capped = apply_exposure_cap([raw[pid] for pid in pids], exposure_cap)
        out.update(zip(pids, capped))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env -i .venv/bin/python -m pytest tests/test_stake.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add optimizer/stake.py tests/test_stake.py
git commit -m "feat(stake): size_slate applies quarter-Kelly + exposure cap per group (§15.9 item 4)"
```

---

### Task 3: Migration `010` — additive `stake` column

**Files:**
- Create: `db/migrations/010_kelly_stake.sql`

**Interfaces:**
- Produces: nullable `parlay_recommendations.stake NUMERIC`.

> **Applied to the LIVE DB by the architect only** (reserved lane; `pg_dump` the schema first). Workers create the file only.

- [ ] **Step 1: Write the migration file**

```sql
-- db/migrations/010_kelly_stake.sql
-- Kelly stake sizing (README §15.9 item 4). Additive, nullable: the ¼-Kelly
-- stake per builder parlay, written by the stake-sizing pass (optimizer/stake.py).
-- settle reads it; NULL means "not sized" and falls back to 1.0u (preserves the
-- prior flat-stake behaviour for historical rows). Budgerr's /parlay-builder/saved
-- does not select this column, so the external contract is byte-unchanged.
ALTER TABLE parlay_recommendations ADD COLUMN IF NOT EXISTS stake NUMERIC;
```

- [ ] **Step 2: Commit**

```bash
git add db/migrations/010_kelly_stake.sql
git commit -m "feat(db): additive nullable stake column for Kelly sizing (§15.9 item 4)"
```

---

### Task 4: The stake-sizing pass — `optimizer/stake.py:main`

**Files:**
- Modify: `optimizer/stake.py` (append DB read/update + `main`/argparse)
- Test: `tests/test_stake_pass.py`

**Interfaces:**
- Consumes: `size_slate` (Task 2); `ingestion.db.get_engine`; `parlay_recommendations.stake` (Task 3).
- Produces: `size_and_persist(engine, *, date=None, exposure_cap=5.0, cap_scope="global", fraction=0.25, bankroll_units=100) -> int` (returns count updated). `main()` parses CLI and calls it.

**Fake-engine pattern:** mirror `tests/test_parlay_builder_api.py` — a fake engine whose `begin()`/`connect()` returns a context-manager connection; `execute` returns a queued result for SELECT and records params for UPDATE. Below is a self-contained minimal version.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stake_pass.py
import pytest
from optimizer.stake import size_and_persist


class _Result:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return self._rows


class _Conn:
    def __init__(self, select_rows, sink):
        self._select_rows = select_rows
        self._sink = sink
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        if sql.strip().startswith("select"):
            return _Result(self._select_rows)
        # UPDATE ... record (parlay_id -> stake)
        self._sink.append(params)
        return _Result([])


class _Engine:
    def __init__(self, select_rows):
        self.select_rows = select_rows
        self.updates = []
    def begin(self): return _Conn(self.select_rows, self.updates)


def test_pass_writes_quarter_kelly_stakes_and_caps():
    # card 1: ~1u edge, card 2: no edge -> 0. Row shape: (parlay_id, p, d, sport)
    rows = [(1, 0.52, 2.0, "mlb"), (2, 0.5, 2.0, "mlb")]
    eng = _Engine(rows)
    n = size_and_persist(eng, exposure_cap=5.0)
    by_pid = {u["pid"]: float(u["stake"]) for u in eng.updates}
    assert n == 2
    assert by_pid[1] == pytest.approx(1.0)
    assert by_pid[2] == 0.0


def test_pass_is_idempotent_recompute_from_scratch():
    rows = [(1, 0.52, 2.0, "mlb")]
    eng = _Engine(rows)
    size_and_persist(eng)
    first = {u["pid"]: float(u["stake"]) for u in eng.updates}
    eng.updates.clear()
    size_and_persist(eng)
    second = {u["pid"]: float(u["stake"]) for u in eng.updates}
    assert first == second
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -i .venv/bin/python -m pytest tests/test_stake_pass.py -v`
Expected: FAIL — `ImportError: cannot import name 'size_and_persist'`

- [ ] **Step 3: Write the implementation**

```python
# append to optimizer/stake.py
import argparse

from sqlalchemy import text

from ingestion.db import get_engine

# Group the slate by ET-local calendar date, matching README §15.10's slate
# reasoning (created_at is an ET timestamptz; a plain ::date in a UTC session
# would split a night). NULL sport (legacy rows) defaults to 'mlb'.
_SELECT = text(
    """
    SELECT pr.parlay_id,
           pr.joint_prob,
           pr.combined_odds,
           COALESCE(pr.legs->>'sport', 'mlb') AS sport
    FROM parlay_recommendations pr
    WHERE pr.kind = 'builder'
      AND (pr.created_at AT TIME ZONE 'America/New_York')::date
          = COALESCE(:date, (now() AT TIME ZONE 'America/New_York')::date)
    """
)

_UPDATE = text("UPDATE parlay_recommendations SET stake = :stake WHERE parlay_id = :pid")


def size_and_persist(engine, *, date=None, exposure_cap=5.0, cap_scope="global",
                     fraction=0.25, bankroll_units=100):
    """Read the given date's (ET, default today) builder parlays, compute the
    quarter-Kelly stake per parlay under the exposure cap, and UPDATE the stake
    column. Idempotent: recomputes from scratch each run. Returns rows updated.
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT, {"date": date}).fetchall()
        sized = size_slate(
            [(int(r[0]), float(r[1]), float(r[2]), r[3]) for r in rows],
            exposure_cap=exposure_cap, cap_scope=cap_scope,
            fraction=fraction, bankroll_units=bankroll_units,
        )
        for pid, stake in sized.items():
            conn.execute(_UPDATE, {"pid": pid, "stake": stake})
    return len(sized)


def main():
    ap = argparse.ArgumentParser(description="Kelly stake sizing for builder parlays (no EV claim).")
    ap.add_argument("--date", default=None, help="ET slate date YYYY-MM-DD (default: today)")
    ap.add_argument("--exposure-cap", type=float, default=5.0, help="same-night total-stake cap in units")
    ap.add_argument("--cap-scope", choices=["global", "per-sport"], default="global")
    ap.add_argument("--kelly-fraction", type=float, default=0.25)
    ap.add_argument("--bankroll-units", type=float, default=100)
    args = ap.parse_args()
    n = size_and_persist(
        get_engine(), date=args.date, exposure_cap=args.exposure_cap,
        cap_scope=args.cap_scope, fraction=args.kelly_fraction, bankroll_units=args.bankroll_units,
    )
    print(f"stake: sized {n} builder parlays (cap {args.exposure_cap}u {args.cap_scope}, "
          f"{args.kelly_fraction:g}-Kelly)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -i .venv/bin/python -m pytest tests/test_stake_pass.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add optimizer/stake.py tests/test_stake_pass.py
git commit -m "feat(stake): size_and_persist pass + CLI (§15.9 item 4)"
```

---

### Task 5: Settle reads the Kelly stake — `modeling/settle.py`

**Files:**
- Modify: `modeling/settle.py:settle_builder_parlays` (candidate SELECT, `parsed`, the settle loop, the INSERT)
- Test: `tests/test_settle_builder.py` (append)

**Interfaces:**
- Consumes: `parlay_recommendations.stake` (Task 3). `parlay_result(leg_results, leg_decimal_odds, stake=1.0)` already accepts `stake=`.
- Produces: `recommendation_outcomes.stake` = the parlay's sized stake (or 1.0 when NULL), and `pnl` scaled by it.

- [ ] **Step 1: Write the failing tests**

Study the existing fake-conn tests in `tests/test_settle_builder.py` (they drive `settle_builder_parlays` against a `_FakeConn`). Add three cases proving stake threading. Mirror the existing test's fixture setup exactly (same helper that seeds a builder parlay + finished stats); the assertions below are the new part:

```python
# append to tests/test_settle_builder.py — using the file's existing fake-conn harness.
# (Reuse the module's existing _run_settle_once/_seed helpers; shown here as pseudocode
#  anchors — match the real helper names already in this file.)

def test_settle_uses_sized_stake_for_pnl_and_recorded_stake(settle_env):
    # a winning 2-leg parlay at combined decimal 2.0, sized stake 0.7
    row = settle_env.seed_builder_parlay(stake=0.7, legs=..., all_hit=True, combined_decimal=2.0)
    out = settle_env.run_settle()
    assert out.recorded_stake == pytest.approx(0.7)
    assert out.recorded_pnl == pytest.approx(0.7 * (2.0 - 1))  # stake*(d-1)


def test_settle_null_stake_falls_back_to_one_unit(settle_env):
    row = settle_env.seed_builder_parlay(stake=None, legs=..., all_hit=True, combined_decimal=2.0)
    out = settle_env.run_settle()
    assert out.recorded_stake == pytest.approx(1.0)
    assert out.recorded_pnl == pytest.approx(1.0 * (2.0 - 1))


def test_settle_zero_stake_books_zero_pnl(settle_env):
    row = settle_env.seed_builder_parlay(stake=0.0, legs=..., all_hit=True, combined_decimal=2.0)
    out = settle_env.run_settle()
    assert out.recorded_stake == 0.0
    assert out.recorded_pnl == 0.0
```

> If the existing harness lacks a `stake` knob, extend its seed helper to accept `stake` and include it in the row `settle_builder_parlays` selects — this is part of this task.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_settle_builder.py -k "sized_stake or null_stake or zero_stake" -v`
Expected: FAIL (stake not selected/threaded yet)

- [ ] **Step 3: Modify `settle_builder_parlays`**

Change 1 — candidate SELECT (add `pr.stake`):

```python
        candidates = conn.execute(
            text(
                """
                SELECT pr.parlay_id, pr.created_at, pr.stake, pr.legs
                FROM parlay_recommendations pr
                WHERE pr.kind = 'builder' AND NOT EXISTS (
                    SELECT 1 FROM recommendation_outcomes ro
                    WHERE ro.bet_type = 'parlay' AND ro.parlay_id = pr.parlay_id)
                """
            )
        ).fetchall()
```

Change 2 — `parsed` (thread `stake`, NULL→1.0):

```python
        parsed = [(pid, ca, (1.0 if stake is None else float(stake)), _as_legs_list(raw))
                  for pid, ca, stake, raw in candidates]
        parsed = [(pid, ca, stake, blob["legs"] if isinstance(blob, dict) else blob)
                  for pid, ca, stake, blob in parsed]
        game_ids = sorted({int(l["game_id"]) for _, _, _stake, legs in parsed for l in legs})
```

Change 3 — the settle loop signature:

```python
        for parlay_id, created_at, stake, legs in parsed:
```

Change 4 — pass stake to `parlay_result` and the INSERT:

```python
            result, decimal_odds, pnl = parlay_result(results, odds_list, stake=stake)
            conn.execute(
                text(
                    """
                    INSERT INTO recommendation_outcomes
                        (bet_type, parlay_id, result, n_legs, stake, decimal_odds, pnl, legs, recommended_at)
                    VALUES ('parlay', :pid, :res, :n, :stake, :co, :pnl, CAST(:legs AS JSONB), :ra)
                    """
                ),
                {"pid": int(parlay_id), "res": result, "n": len(legs), "stake": stake,
                 "co": float(decimal_odds), "pnl": float(pnl),
                 "legs": json.dumps(audit), "ra": created_at})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_settle_builder.py -v`
Expected: PASS (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add modeling/settle.py tests/test_settle_builder.py
git commit -m "feat(settle): book Kelly stake (NULL->1.0 fallback), pnl scales with it (§15.9 item 4)"
```

---

### Task 6: Record ROI = pnl/staked — `api/main.py`

**Files:**
- Modify: `api/main.py:_shape_builder_record` (`:481`), `builder_record` query (`:511`), `_shape_builder_record_daily` (`:530`), `builder_record_daily` query (`:560`)
- Modify: `api/schemas.py` — add additive `staked: float` to `BuilderRecordOut` and `BuilderRecordDailyOut`
- Test: `tests/test_parlay_builder_api.py` (append)

**Interfaces:**
- Consumes: `recommendation_outcomes.stake` (now variable, Task 5).
- Produces: `roi = pnl/staked` in both record endpoints; additive `staked` field on both output models.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_parlay_builder_api.py
from api.main import _shape_builder_record, _shape_builder_record_daily


def test_builder_record_roi_is_stake_weighted():
    # 2 wins: one staked 1.0 pnl +0.4, one staked 0.5 pnl +0.5. ROI = 0.9/1.5, NOT 0.9/2.
    rows = [("across_game", 1.4, 2, 2, 0, 0, 1.5, 0.9)]  # (cls,tp,n,wins,losses,pushes,staked,pnl)
    out = _shape_builder_record(rows)
    assert out[0].staked == pytest.approx(1.5)
    assert out[0].roi == pytest.approx(0.9 / 1.5)


def test_builder_record_roi_zero_when_no_stake():
    rows = [("across_game", 1.4, 1, 0, 1, 0, 0.0, 0.0)]
    out = _shape_builder_record(rows)
    assert out[0].roi == 0.0


def test_builder_record_daily_roi_is_stake_weighted():
    rows = [("2026-08-07", 2, 2, 0, 0, 1.5, 0.9)]  # (date,n,wins,losses,pushes,staked,pnl)
    out = _shape_builder_record_daily(rows)
    assert out[0].staked == pytest.approx(1.5)
    assert out[0].roi == pytest.approx(0.9 / 1.5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_parlay_builder_api.py -k "stake_weighted or no_stake" -v`
Expected: FAIL (shapers don't accept a staked column / `staked` field missing)

- [ ] **Step 3a: Add the additive schema fields**

In `api/schemas.py`, add to `BuilderRecordOut` and `BuilderRecordDailyOut`:

```python
    staked: float = 0.0
```

- [ ] **Step 3b: Update `_shape_builder_record` (row now carries staked before pnl)**

```python
def _shape_builder_record(rows):
    """Pure: rows are (cls, target_payout, n, wins, losses, pushes, staked, pnl)
    as produced by the GROUP BY in builder_record() below. Maps cls->tier via
    _CLASS_TO_TIER, computes roi=pnl/staked (0.0 when staked==0) so variable
    Kelly stakes aggregate correctly, casts Decimals to float, orders
    player-before-team then ascending target_payout. DB-free.
    """
    shaped = []
    for cls, target_payout, n, wins, losses, pushes, staked, pnl in rows:
        tier = _CLASS_TO_TIER.get(cls, cls)
        n = int(n)
        staked = float(staked or 0)
        pnl = float(pnl or 0)
        shaped.append(
            BuilderRecordOut(
                tier=tier, target_payout=float(target_payout),
                n=n, wins=int(wins), losses=int(losses), pushes=int(pushes),
                staked=staked, pnl=pnl, roi=(pnl / staked if staked else 0.0),
            )
        )
    shaped.sort(key=lambda r: (_TIER_SORT_ORDER.get(r.tier, 2), r.target_payout))
    return shaped
```

- [ ] **Step 3c: Add `sum(ro.stake)` to the `builder_record` query** (before `sum(ro.pnl)`):

```python
            SELECT pr.legs->>'class' AS cls, pr.target_payout,
                   count(*) AS n,
                   sum((ro.result='win')::int)  AS wins,
                   sum((ro.result='loss')::int) AS losses,
                   sum((ro.result='push')::int) AS pushes,
                   sum(ro.stake) AS staked,
                   sum(ro.pnl) AS pnl
            FROM recommendation_outcomes ro
            JOIN parlay_recommendations pr ON pr.parlay_id = ro.parlay_id
            WHERE pr.kind = 'builder'
              AND COALESCE(pr.legs->>'sport', 'mlb') = :sport
            GROUP BY 1, 2
            ORDER BY 1, 2
```

- [ ] **Step 3d: Update `_shape_builder_record_daily`** (row now carries staked before pnl):

```python
def _shape_builder_record_daily(rows):
    """Pure: rows are (slate_date, n, wins, losses, pushes, staked, pnl) as
    produced by the GROUP BY date(pr.created_at) in builder_record_daily().
    Computes roi=pnl/staked (0.0 when staked==0), casts Decimal pnl to float,
    stringifies the date. Newest-first order from SQL is preserved. DB-free.
    """
    shaped = []
    for slate_date, n, wins, losses, pushes, staked, pnl in rows:
        n = int(n)
        staked = float(staked or 0)
        pnl = float(pnl or 0)
        shaped.append(
            BuilderRecordDailyOut(
                date=str(slate_date), n=n, wins=int(wins), losses=int(losses),
                pushes=int(pushes), staked=staked, pnl=pnl,
                roi=(pnl / staked if staked else 0.0),
            )
        )
    return shaped
```

- [ ] **Step 3e: Add `sum(ro.stake)` to the `builder_record_daily` query** (before `sum(ro.pnl)`):

```python
            SELECT date(pr.created_at) AS slate_date, count(*) n,
                   sum((ro.result='win')::int) wins, sum((ro.result='loss')::int) losses,
                   sum((ro.result='push')::int) pushes, sum(ro.stake) staked, sum(ro.pnl) pnl
            FROM recommendation_outcomes ro JOIN parlay_recommendations pr ON pr.parlay_id=ro.parlay_id
            WHERE pr.kind='builder'
              AND COALESCE(pr.legs->>'sport', 'mlb') = :sport
            GROUP BY 1 ORDER BY 1 DESC
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_parlay_builder_api.py -v`
Expected: PASS (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add api/main.py api/schemas.py tests/test_parlay_builder_api.py
git commit -m "feat(api): builder-record ROI = pnl/staked for variable Kelly stakes (§15.9 item 4)"
```

---

### Task 7: Wire the stake pass into the chain + dashboard copy + README

> **Architect-only** (touches the live chain + docs). Not delegated.

**Files:**
- Modify: `scripts/daily_chain.sh` (add a `stake` step after the four MLB builder saves, before the multi-sport block)
- Modify: `web/app/builder/RecordPanel.tsx` (label staked units; honest "stake sizing" copy — no +EV/green)
- Modify: `README.md` §15.9 item 4

- [ ] **Step 1: Add the stake step to the chain** (after `builder_team_2.0`, inside the core group):

```bash
		_step builder_team_2.0 "$PY" -m optimizer.builder --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
		_step stake            "$PY" -m optimizer.stake &&
```

(The pass sizes the whole date's builder rows — all four saves are done by now — before `settle`. It is idempotent, so a re-run is safe.)

- [ ] **Step 2: Verify chain syntax**

Run: `bash -n scripts/daily_chain.sh`
Expected: no output (syntax OK)

- [ ] **Step 3: Dashboard copy** — in `web/app/builder/RecordPanel.tsx`, surface `staked` (e.g. "N bets · X.Xu staked") next to the record. Keep the existing paper/small-sample caption. NO "+EV"/"edge"/"value" wording, no signal-green. Frame the stake as "¼-Kelly stake sizing".

- [ ] **Step 4: README §15.9 item 4** — replace the one-line placeholder with a BUILT & DEPLOYED entry: the edge definition, unit convention, cap (default 5u global, configurable), the pass + migration 010 + settle NULL→1.0 fallback + record ROI fix, the honest framing, and the live 08-07 measurement (3/8 cards stake ~0.35–0.43u, 5/8 zero). Note stakes are additive/paper-only and Budgerr byte-unchanged.

- [ ] **Step 5: Full suite + commit**

```bash
.venv/bin/python -m pytest -q
git add scripts/daily_chain.sh web/app/builder/RecordPanel.tsx README.md
git commit -m "feat(chain+web): run stake pass pre-settle; show staked units; README §15.9 item 4"
```

---

## Architect execution steps (after all tasks reviewed + merged)

1. `pg_dump --schema-only` the live DB (backup), then apply `db/migrations/010_kelly_stake.sql`.
2. Run `.venv/bin/python -m optimizer.stake` once for today's slate; verify `parlay_recommendations.stake` populated (spot-check 3/8 cards ≈ the measured 0.35–0.43u; 5/8 = 0).
3. Kickstart the API (`launchctl kickstart -k gui/$(id -u)/com.playstat.api`) — `api/main.py` changed (record shapers).
4. Hit `/parlay-builder/record` live; confirm ROI is stake-weighted and `staked` present.
5. Browser-verify the dashboard record panel renders staked units, no green/EV language.
6. `graphify update .`, then `git push origin main`.

## Self-review notes

- **Spec coverage:** edge formula (T1), unit scaling (T1), cap + scope (T2), pass/idempotency (T4), migration (T3), settle NULL→1.0 (T5), ROI fix both shapers (T6), chain wiring + honest copy + README (T7). Decisions (a) stake=0 recorded normally → falls out of T5 (0 stake books 0 pnl, still inserted); (b) stake not on live card/Budgerr → nothing added to `/parlay-builder/saved`. All covered.
- **Types:** `size_slate` returns `dict[int,float]`; `size_and_persist` returns `int`; row tuples `(parlay_id,p,decimal_odds,sport)` consistent T2↔T4; record rows gain `staked` before `pnl` consistently in query + shaper (T6).
- **No placeholders** except the T5 test which intentionally anchors to the existing fake-conn harness names (the worker must match the real helper in that file — called out explicitly).
