# NFL #4a — backend chain + record sport-filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make NFL builder parlays build (weekly), settle, and report on their own cadence end-to-end inside the existing daily chain — no dashboard changes (that is #4b).

**Architecture:** Three components. (A) Generalize the builder's single-day slate filter to a per-sport date-range window (NFL = Thu–Mon). (C) Add an additive `?sport` filter to the two builder record endpoints. (B) Wire NFL steps into `scripts/daily_chain.sh` (Thursday-gated build + daily score ingestion) — the **architect's** reserved lane, listed here for completeness, NOT for the agent.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, pytest, PostgreSQL. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-07-29-nfl-chain-record-design.md](../specs/2026-07-29-nfl-chain-record-design.md)

## Global Constraints

- **NO TEST DATABASE. `ingestion.db.get_engine()` is the LIVE production DB.** Every test is pure or uses fake-engine isolation (monkeypatch `api.main.engine`); a test that opens a real socket/connection is a defect. Grep new tests for `get_engine`/`create_engine`/`psycopg2` before finishing.
- **MLB behavior must not change.** `window_days=0` (the MLB default) makes `BETWEEN base AND base + 0` semantically identical to today's `= base`. Verify by reasoning + the read-only architect check; do not regress MLB.
- **Additive-only API.** The `?sport` param defaults to `"mlb"`; existing no-`sport` callers are unaffected. The record endpoints are dashboard-only (not a Budgerr surface), but stay additive anyway.
- **Guardrails §15.8:** no "+EV"/"edge"/"value"/"beat the market" language; no signal-green. This change introduces no new claims.
- **DO NOT edit `scripts/daily_chain.sh`** (Component B is the architect's). **DO NOT `git push`.** Commit in the worktree only.
- **Worktree:** `graphify-out/` is gitignored/absent — read source directly (do not try to graphify). Interpreter: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python`, run from the worktree cwd. `.env` not needed (tests pure/fake-engine). Baseline suite: **332 passing**.

---

## Task 1: Per-sport builder slate window

**Files:**
- Modify: `optimizer/builder.py` (module const + `load_player_legs`, `load_team_legs`, `load_legs`, `main`)
- Test: `tests/test_builder.py` (update 2 existing assertions + add new tests)

**Interfaces:**
- Produces: `SLATE_WINDOW_DAYS: dict[str,int]` (`{"mlb":0,"nfl":4}`); `load_player_legs(engine, floor=..., slate_date=None, sport="mlb", window_days=0)`; same trailing `window_days=0` on `load_team_legs` and `load_legs`; a `--window-days` CLI flag (default `None` ⇒ per-sport default).

- [ ] **Step 1: Update the two existing literal-string tests to the range predicate.**

In `tests/test_builder.py`, `test_games_join_has_date_predicate_defaulting_to_current_date` currently asserts `"g.date = COALESCE(:slate_date, CURRENT_DATE)" in source`. Change that one assertion to:

```python
    assert "g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)" in source
```

In `test_loaders_filter_games_join_by_sport`, change the same
`"g.date = COALESCE(:slate_date, CURRENT_DATE)"` assertion identically to
`"g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)"`. Leave the `g.status != 'FT'`
and `g.sport = :sport` assertions unchanged in both.

- [ ] **Step 2: Add new failing tests** (`tests/test_builder.py`):

```python
def test_slate_window_days_map_has_mlb_zero_nfl_four():
    assert builder.SLATE_WINDOW_DAYS["mlb"] == 0
    assert builder.SLATE_WINDOW_DAYS["nfl"] == 4


import inspect as _inspect
import pytest as _pytest

@_pytest.mark.parametrize("fn", [builder.load_player_legs, builder.load_team_legs, builder.load_legs])
def test_loaders_have_window_days_param_defaulting_to_zero(fn):
    sig = _inspect.signature(fn)
    assert "window_days" in sig.parameters
    assert sig.parameters["window_days"].default == 0


@_pytest.mark.parametrize("fn", [builder.load_player_legs, builder.load_team_legs])
def test_loaders_thread_window_days_into_query_params(fn):
    src = _inspect.getsource(fn)
    # upper bound of the BETWEEN range uses the bound param
    assert "COALESCE(:slate_date, CURRENT_DATE) + :window_days" in src
    assert '"window_days": window_days' in src


def test_main_resolves_window_days_from_sport(monkeypatch):
    # --sport nfl with no --window-days -> build() gets window_days from the map (4)
    captured = {}
    monkeypatch.setattr("optimizer.builder.db.get_engine", lambda: object())
    monkeypatch.setattr("optimizer.builder.load_legs",
                        lambda engine, floor, slate_date, sport, window_days: captured.update(window_days=window_days) or [{"x": 1}])
    monkeypatch.setattr("optimizer.builder.build", lambda *a, **k: [])
    monkeypatch.setattr("sys.argv", ["builder", "--target-payout", "1.4", "--sport", "nfl"])
    from optimizer.builder import main
    main()
    assert captured["window_days"] == 4


def test_main_window_days_flag_overrides_sport_default(monkeypatch):
    captured = {}
    monkeypatch.setattr("optimizer.builder.db.get_engine", lambda: object())
    monkeypatch.setattr("optimizer.builder.load_legs",
                        lambda engine, floor, slate_date, sport, window_days: captured.update(window_days=window_days) or [{"x": 1}])
    monkeypatch.setattr("optimizer.builder.build", lambda *a, **k: [])
    monkeypatch.setattr("sys.argv", ["builder", "--target-payout", "1.4", "--sport", "nfl", "--window-days", "0"])
    from optimizer.builder import main
    main()
    assert captured["window_days"] == 0
```

> Note: `load_legs` currently calls `load_player_legs(engine, floor, slate_date, sport)` and `load_team_legs(...)` positionally. After adding the trailing `window_days` param it must pass it through. `main()` passes `window_days=` as a keyword to `load_legs`/`load_team_legs` — the monkeypatched `load_legs` above takes it positionally-or-keyword to match.

- [ ] **Step 3: Run the new tests, verify they fail.**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder.py -q -k "window or slate_window"`
Expected: failures (`SLATE_WINDOW_DAYS`/`window_days` not defined).

- [ ] **Step 4: Implement.** In `optimizer/builder.py`:

Add the module constant near `TEAM_MARKETS`:
```python
# Per-sport slate window (days added to the lower bound). MLB bets a single day's
# slate; NFL bets a weekly Thu..Mon card (see 2026-07-29-nfl-chain-record spec).
SLATE_WINDOW_DAYS = {"mlb": 0, "nfl": 4}
```

In BOTH `load_player_legs` and `load_team_legs`: add `window_days=0` as the last
param; change the games-join date predicate from
`AND g.date = COALESCE(:slate_date, CURRENT_DATE)` to
```
                    AND g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)
                                   AND COALESCE(:slate_date, CURRENT_DATE) + :window_days
```
and add `"window_days": window_days` to that query's `params={...}` dict.

In `load_legs`: add `window_days=0` param and thread it:
```python
def load_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb", window_days=0):
    return (load_player_legs(engine, floor, slate_date, sport, window_days)
            + load_team_legs(engine, floor, slate_date, sport, window_days))
```

In `main()`: add the CLI flag and resolve the window:
```python
    parser.add_argument("--window-days", type=int, default=None,
                        help="slate window length in days added to the lower bound "
                             "(default: per-sport — mlb 0 = today only, nfl 4 = Thu..Mon "
                             "weekly card). Override to force a specific span.")
```
After `args = parser.parse_args()` (and after the existing target/min-prob check), resolve:
```python
    window_days = args.window_days if args.window_days is not None else SLATE_WINDOW_DAYS.get(args.sport, 0)
```
Then pass `window_days` into the loader calls: in the `--team-only` branch
`load_team_legs(engine, args.floor, args.slate_date, args.sport, window_days)`, else
`load_legs(engine, args.floor, args.slate_date, args.sport, window_days)`.

> Postgres `date + integer` adds days; psycopg2 binds a Python int as integer, so `COALESCE(:slate_date, CURRENT_DATE) + :window_days` types correctly. If a "operator does not exist: date + unknown" error ever appears, wrap as `+ CAST(:window_days AS integer)` — functionally identical; update the Step 2 source assertion to match if you do.

- [ ] **Step 5: Run the updated + new tests, verify pass.**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder.py -q`
Expected: all pass (the 2 updated assertions + 5 new).

- [ ] **Step 6: Run the full suite.**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest -q`
Expected: 332 + new, all green.

- [ ] **Step 7: Commit.**

```bash
git add optimizer/builder.py tests/test_builder.py
git commit -m "feat(builder): per-sport slate window (NFL weekly Thu-Mon; MLB unchanged)"
```

---

## Task 2: Record endpoints `?sport` filter

**Files:**
- Modify: `api/main.py` (`builder_record`, `builder_record_daily`)
- Test: `tests/test_builder_record_api.py` (add tests)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `builder_record(sport: str = "mlb")`, `builder_record_daily(sport: str = "mlb")`; both SQL statements gain `AND COALESCE(pr.legs->>'sport', 'mlb') = :sport` and pass `params={"sport": sport}`.

- [ ] **Step 1: Write failing tests** (`tests/test_builder_record_api.py`).

Add a params-capturing fake (the file's `_FakeConn.execute` ignores its args, so add a capturing variant) and behavioral/signature tests:

```python
import inspect


def test_record_endpoints_have_sport_param_defaulting_to_mlb():
    for fn in (api_main.builder_record, api_main.builder_record_daily):
        sig = inspect.signature(fn)
        assert sig.parameters["sport"].default == "mlb"


def test_record_sql_has_sport_coalesce_filter():
    for fn in (api_main.builder_record, api_main.builder_record_daily):
        src = inspect.getsource(fn)
        assert "COALESCE(pr.legs->>'sport', 'mlb') = :sport" in src


class _CapturingConn:
    def __init__(self, rows, calls):
        self._rows, self._calls = rows, calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        self._calls.append(params)
        return _FakeResult(self._rows)


class _CapturingEngine:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def begin(self):
        return _CapturingConn(self.rows, self.calls)


def test_builder_record_threads_sport_param(monkeypatch):
    eng = _CapturingEngine([("across_game", 1.4, 25, 18, 6, 1, -0.14)])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record(sport="nfl")
    assert eng.calls[0]["sport"] == "nfl"


def test_builder_record_daily_threads_sport_param(monkeypatch):
    eng = _CapturingEngine([("2026-09-11", 5, 3, 2, 0, 1.2)])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record_daily(sport="nfl")
    assert eng.calls[0]["sport"] == "nfl"


def test_builder_record_defaults_sport_to_mlb(monkeypatch):
    eng = _CapturingEngine([])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record()
    assert eng.calls[0]["sport"] == "mlb"
```

- [ ] **Step 2: Run, verify failure.**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder_record_api.py -q -k "sport"`
Expected: FAIL (no `sport` param / filter yet; `execute` called without a `params` kwarg).

- [ ] **Step 3: Implement** (`api/main.py`).

`builder_record`: add `sport: str = "mlb"` to the signature; in the SQL change
`WHERE pr.kind = 'builder'` to
```
            WHERE pr.kind = 'builder'
              AND COALESCE(pr.legs->>'sport', 'mlb') = :sport
```
and change the `conn.execute(text(...))` call to pass params:
`conn.execute(text(...), {"sport": sport})`.

`builder_record_daily`: identical treatment — add `sport: str = "mlb"`, add the same
`AND COALESCE(pr.legs->>'sport', 'mlb') = :sport` after its `WHERE pr.kind='builder'`,
and pass `{"sport": sport}` to `conn.execute`.

> The FastAPI route decorators are unchanged; `sport` becomes an optional query
> param automatically (`GET /parlay-builder/record?sport=nfl`). The pure
> `_shape_*` helpers are untouched.

- [ ] **Step 4: Run the new tests + existing ones, verify pass.**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder_record_api.py -q`
Expected: all pass (existing endpoint tests still green — they call with no `sport`, defaulting to `"mlb"`, and the capturing/`_FakeEngine` return the queued rows regardless of the filter).

- [ ] **Step 5: Run the full suite.**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest -q`
Expected: all green.

- [ ] **Step 6: Commit.**

```bash
git add api/main.py tests/test_builder_record_api.py
git commit -m "feat(api): additive ?sport filter on /parlay-builder/record + /record/daily"
```

---

## Task 3 (ARCHITECT ONLY — not the agent): daily-chain NFL wiring + kickstart

Listed for completeness; the agent must NOT do this. After Tasks 1–2 merge, the architect edits `scripts/daily_chain.sh` inside `run_chain`:

- A **Thursday-gated** build block (`if [ "$(date +%u)" -eq 4 ]; then ... fi`): `_step_retry nfl_odds ... odds_ingest --sport nfl`; four `_step nfl_builder_* ... optimizer.builder --sport nfl [--team-only] --target-payout 1.4/2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save`.
- A **daily** `_step_retry nfl_scores "$PY" -m ingestion.nfl_backfill --only games`, placed before `settle`.
- `bash -n` the script; smoke-test the Thursday gate (force `date`), confirm MLB steps unchanged, kickstart `com.playstat.api` (the `api/main.py` change), and do the read-only live check (`load_legs(sport='nfl', window_days=4)` → `[]` cleanly; MLB `load_legs()` count unchanged).

---

## Self-Review (completed by plan author)

- **Spec coverage:** Component A → Task 1; Component C → Task 2; Component B → Task 3 (architect). Verification items map to Task 1/2 test steps + Task 3 architect checks. All covered.
- **Placeholder scan:** none — every code step shows the actual code/assertion.
- **Type consistency:** `window_days` (int, default 0) and `SLATE_WINDOW_DAYS` used identically across loaders/`main`/tests; `sport: str = "mlb"` identical across both record endpoints/tests; `COALESCE(pr.legs->>'sport', 'mlb') = :sport` string identical in impl and the source-inspection assertions.
- **Live-DB safety:** Task 1 loader tests are source-inspection/signature only (the SQL executes against the live DB, so it is NOT run in tests); Task 2 uses fake/capturing engines monkeypatched over `api.main.engine`. No real connection anywhere.

## Execution Handoff

Architect dispatches a single worktree subagent for Tasks 1–2 (review diffs between/after), then performs Task 3 (chain + kickstart + live check) and merges.
