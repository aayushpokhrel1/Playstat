# Per-day Parlay Drill-down Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expand a slate day in the builder record to its settled parlays, and expand a parlay to its per-leg ✓/✗ (from the settlement audit).

**Architecture:** A new dashboard-only endpoint `GET /parlay-builder/record/daily/parlays` returns a date's settled builder parlays, each with legs merged from the recommendation (labels/team context) and the settlement audit (result/actual) by `builder_leg_key`. Pure shaper + merge helper (fake-engine tested). `RecordPanel.tsx` gains two nested expanders. Additive-only; not a Budgerr surface.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy Core, pytest; Next.js (client component), TypeScript.

## Global Constraints

- **Additive-only.** New endpoint/schemas/TS types/UI. No change to `/parlay-builder/saved`, `/games`, `/box-scores`, `/record`, `/record/daily` shapes. Budgerr byte-unchanged.
- **No test DB.** `get_engine()` is LIVE. Pure helpers unit-tested; endpoint via the fake-engine/queue pattern in `tests/test_builder_record_api.py`.
- **Honest framing (§15.8 #2):** paper-only, neutral W/L/P chips, NO +EV/edge/green (green stays reserved for the ≥75% joint-prob rule).
- **`graphify query` before reading source** (main checkout only; read source directly in a worktree). `graphify update .` after code changes.
- **Architect-only:** `git push`; `launchctl kickstart -k gui/$(id -u)/com.playstat.api` after this `api/main.py` change; browser verification.
- **web/AGENTS.md:** this Next.js (16.x) differs from training data — read `web/node_modules/next/dist/docs/` before writing Next-specific code. (This task only adds a client-side fetch + presentational nesting; no server/routing APIs.)

---

### Task 1: Response schemas — `api/schemas.py`

**Files:**
- Modify: `api/schemas.py` (add two models near `BuilderRecordDailyOut`)

**Interfaces:**
- Produces: `DailyParlayLegOut`, `DailyParlayOut`.

- [ ] **Step 1: Add the models**

```python
class DailyParlayLegOut(BaseModel):
    """One leg of a settled builder parlay in the per-day drill-down: label +
    team context from the recommendation, result/actual from the settlement
    audit (README per-day drill-down spec)."""

    label: str | None = None
    side: str | None = None
    line: float | None = None
    actual: float | None = None
    result: str | None = None       # hit/won -> ✓, miss/lost -> ✗, void -> –, None -> pending
    odds: int | None = None
    book: str | None = None
    home_team: str | None = None
    away_team: str | None = None


class DailyParlayOut(BaseModel):
    """One settled builder parlay for a slate date (dashboard-only drill-down)."""

    parlay_id: int
    result: str                     # win | loss | push
    tier: str                       # player | team | game
    target_payout: float
    combined_odds: float
    stake: float
    pnl: float
    legs: list[DailyParlayLegOut]
```

- [ ] **Step 2: Commit**

```bash
git add api/schemas.py
git commit -m "feat(api): DailyParlayOut/LegOut schemas for per-day drill-down"
```

---

### Task 2: Leg merge helper — `api/main.py:_merge_parlay_legs`

**Files:**
- Modify: `api/main.py` (add helper; import `builder_leg_key`)
- Test: `tests/test_builder_record_api.py` (append)

**Interfaces:**
- Consumes: `modeling.settle.builder_leg_key`, `DailyParlayLegOut` (Task 1).
- Produces: `_merge_parlay_legs(rec_legs: list[dict], audit_legs: list[dict]) -> list[DailyParlayLegOut]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_builder_record_api.py
from api.schemas import DailyParlayLegOut


def test_merge_parlay_legs_joins_label_from_rec_and_result_from_audit():
    rec = [
        {"kind": "player", "game_id": 5, "player_id": 9, "stat_type": "hits",
         "label": "Aaron Judge hits over 1.5", "side": "over", "line": 1.5, "odds": -120,
         "home_team": "NYY", "away_team": "BOS"},
        {"kind": "team", "game_id": 7, "market": "f5_runs",
         "label": "f5_runs under 5.5", "side": "under", "line": 5.5, "odds": -143},
    ]
    audit = [
        {"kind": "team", "game_id": 7, "market": "f5_runs", "actual": 4.0, "result": "hit"},
        {"kind": "player", "game_id": 5, "player_id": 9, "stat_type": "hits",
         "actual": 2.0, "result": "hit"},
    ]
    out = api_main._merge_parlay_legs(rec, audit)
    assert [l.label for l in out] == ["Aaron Judge hits over 1.5", "f5_runs under 5.5"]  # rec order
    assert out[0].actual == 2.0 and out[0].result == "hit"      # matched across list order
    assert out[0].home_team == "NYY"
    assert out[1].actual == 4.0 and out[1].result == "hit"


def test_merge_parlay_legs_audit_missing_leaves_result_none():
    rec = [{"kind": "team", "game_id": 7, "market": "f5_runs", "label": "x", "side": "under",
            "line": 5.5, "odds": -143}]
    out = api_main._merge_parlay_legs(rec, [])
    assert out[0].result is None and out[0].actual is None
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_builder_record_api.py -k merge_parlay -v`
Expected: FAIL — `_merge_parlay_legs` not defined.

- [ ] **Step 3: Implement (add near the other builder-record helpers in `api/main.py`)**

```python
from modeling.settle import builder_leg_key  # add to existing settle import if present


def _merge_parlay_legs(rec_legs, audit_legs):
    """Join recommendation legs (label + team context) with settlement audit
    legs (result/actual) by builder_leg_key. Order follows the recommendation.
    Audit-missing legs keep result/actual None (shouldn't happen for a settled
    parlay). DB-free."""
    audit_by_key = {}
    for a in audit_legs:
        try:
            audit_by_key[builder_leg_key(a)] = a
        except (ValueError, KeyError, TypeError):
            continue
    merged = []
    for r in rec_legs:
        try:
            a = audit_by_key.get(builder_leg_key(r), {})
        except (ValueError, KeyError, TypeError):
            a = {}
        merged.append(DailyParlayLegOut(
            label=r.get("label"), side=r.get("side"), line=r.get("line"),
            actual=a.get("actual"), result=a.get("result"),
            odds=r.get("odds"), book=r.get("book"),
            home_team=r.get("home_team"), away_team=r.get("away_team"),
        ))
    return merged
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_builder_record_api.py -k merge_parlay -v`
Expected: PASS (2)

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_builder_record_api.py
git commit -m "feat(api): _merge_parlay_legs joins rec labels + audit results by key"
```

---

### Task 3: Pure shaper — `api/main.py:_shape_daily_parlays`

**Files:**
- Modify: `api/main.py`
- Test: `tests/test_builder_record_api.py` (append)

**Interfaces:**
- Consumes: `_merge_parlay_legs` (T2), `_CLASS_TO_TIER`, `_as_legs_list`, `DailyParlayOut`.
- Produces: `_shape_daily_parlays(rows) -> list[DailyParlayOut]` where each row is
  `(parlay_id, result, cls, target_payout, combined_odds, stake, pnl, rec_legs, audit_legs)`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_builder_record_api.py
from api.schemas import DailyParlayOut


def test_shape_daily_parlays_maps_tier_and_merges_legs():
    rec_wrapper = {"class": "team_tier", "sport": "mlb", "legs": [
        {"kind": "team", "game_id": 7, "market": "f5_runs", "label": "f5_runs under 5.5",
         "side": "under", "line": 5.5, "odds": -143}]}
    audit = [{"kind": "team", "game_id": 7, "market": "f5_runs", "actual": 4.0, "result": "hit"}]
    rows = [(297, "loss", "team_tier", Decimal("1.4"), Decimal("2.913"), Decimal("1.0"),
             Decimal("-1.0"), rec_wrapper, audit)]
    out = api_main._shape_daily_parlays(rows)
    assert len(out) == 1
    p = out[0]
    assert isinstance(p, DailyParlayOut)
    assert p.tier == "team" and p.result == "loss"
    assert p.stake == 1.0 and p.pnl == -1.0 and p.combined_odds == 2.913
    assert p.legs[0].label == "f5_runs under 5.5" and p.legs[0].result == "hit"


def test_shape_daily_parlays_empty_rows_returns_empty_list():
    assert api_main._shape_daily_parlays([]) == []
```

- [ ] **Step 2: Run to verify fail** — `pytest ... -k shape_daily_parlays -v` → FAIL.

- [ ] **Step 3: Implement**

```python
def _shape_daily_parlays(rows):
    """Pure: rows are (parlay_id, result, cls, target_payout, combined_odds,
    stake, pnl, rec_legs, audit_legs). rec_legs is the {class,legs,sport}
    wrapper; audit_legs is the settlement audit list. Maps cls->tier, casts
    Decimals to float, merges legs. DB-free."""
    out = []
    for parlay_id, result, cls, target_payout, combined_odds, stake, pnl, rec_legs, audit_legs in rows:
        blob = _as_legs_list(rec_legs)
        rec = blob["legs"] if isinstance(blob, dict) else blob
        audit = _as_legs_list(audit_legs)
        out.append(DailyParlayOut(
            parlay_id=int(parlay_id), result=result,
            tier=_CLASS_TO_TIER.get(cls, cls),
            target_payout=float(target_payout), combined_odds=float(combined_odds),
            stake=float(stake or 0), pnl=float(pnl or 0),
            legs=_merge_parlay_legs(rec, audit),
        ))
    return out
```

- [ ] **Step 4: Run to verify pass** — PASS (2).

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_builder_record_api.py
git commit -m "feat(api): _shape_daily_parlays pure shaper for the drill-down"
```

---

### Task 4: Endpoint — `GET /parlay-builder/record/daily/parlays`

**Files:**
- Modify: `api/main.py` (route after `builder_record_daily`)
- Test: `tests/test_builder_record_api.py` (append)

**Interfaces:**
- Consumes: `_shape_daily_parlays` (T3).
- Produces: `builder_record_daily_parlays(date: str, sport: str = "mlb") -> list[DailyParlayOut]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_builder_record_api.py
def test_daily_parlays_endpoint_shapes_rows(monkeypatch):
    rec = {"class": "across_game", "sport": "mlb", "legs": [
        {"kind": "player", "game_id": 5, "player_id": 9, "stat_type": "hits",
         "label": "J hits o1.5", "side": "over", "line": 1.5, "odds": -120}]}
    audit = [{"kind": "player", "game_id": 5, "player_id": 9, "stat_type": "hits",
              "actual": 2.0, "result": "hit"}]
    rows = [(1, "win", "across_game", Decimal("1.4"), Decimal("1.4"), Decimal("0.5"),
             Decimal("0.2"), rec, audit)]
    eng = _CapturingEngine(rows)
    monkeypatch.setattr(api_main, "engine", eng)
    out = api_main.builder_record_daily_parlays(date="2026-08-06")
    assert len(out) == 1 and out[0].result == "win"
    assert out[0].legs[0].result == "hit"
    assert eng.calls[0]["sport"] == "mlb" and eng.calls[0]["date"] == "2026-08-06"


def test_daily_parlays_endpoint_threads_sport(monkeypatch):
    eng = _CapturingEngine([])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record_daily_parlays(date="2026-08-06", sport="nfl")
    assert eng.calls[0]["sport"] == "nfl"
```

- [ ] **Step 2: Run to verify fail** — endpoint not defined.

- [ ] **Step 3: Implement**

```python
@app.get("/parlay-builder/record/daily/parlays", response_model=list[DailyParlayOut])
def builder_record_daily_parlays(date: str, sport: str = "mlb"):
    """Settled builder parlays for one slate date, each with per-leg result
    (README per-day drill-down). Dashboard-only; additive; not a Budgerr
    surface. date is YYYY-MM-DD; sport defaults to mlb like the other record
    endpoints."""
    with engine.begin() as conn:
        rows = conn.execute(text(
            """
            SELECT ro.parlay_id, ro.result, pr.legs->>'class' AS cls,
                   pr.target_payout, ro.decimal_odds, ro.stake, ro.pnl,
                   pr.legs AS rec_legs, ro.legs AS audit_legs
            FROM recommendation_outcomes ro
            JOIN parlay_recommendations pr ON pr.parlay_id = ro.parlay_id
            WHERE pr.kind = 'builder'
              AND COALESCE(pr.legs->>'sport', 'mlb') = :sport
              AND date(pr.created_at) = :date
            ORDER BY ro.parlay_id DESC
            """
        ), {"sport": sport, "date": date}).fetchall()
    return _shape_daily_parlays(rows)
```

- [ ] **Step 4: Run to verify pass** — PASS (2). Then full record file: `pytest tests/test_builder_record_api.py -q`.

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_builder_record_api.py
git commit -m "feat(api): GET /parlay-builder/record/daily/parlays drill-down endpoint"
```

---

### Task 5: Web API client — `web/app/lib/api.ts`

**Files:**
- Modify: `web/app/lib/api.ts`

**Interfaces:**
- Produces: `DailyParlay`, `DailyParlayLeg` types; `getDailyParlays(date, sport?)`.

- [ ] **Step 1: Add types + fetcher**

```typescript
export type DailyParlayLeg = {
  label: string | null;
  side: string | null;
  line: number | null;
  actual: number | null;
  result: string | null; // hit/won -> ✓, miss/lost -> ✗, void -> –, null -> pending
  odds: number | null;
  book: string | null;
  home_team: string | null;
  away_team: string | null;
};

export type DailyParlay = {
  parlay_id: number;
  result: "win" | "loss" | "push";
  tier: "player" | "team" | "game";
  target_payout: number;
  combined_odds: number;
  stake: number;
  pnl: number;
  legs: DailyParlayLeg[];
};

export function getDailyParlays(date: string, sport = "mlb") {
  return apiGet<DailyParlay[]>(
    `/parlay-builder/record/daily/parlays?date=${date}&sport=${sport}`,
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd web && npx tsc --noEmit` → clean.

- [ ] **Step 3: Commit**

```bash
git add web/app/lib/api.ts
git commit -m "feat(web): DailyParlay types + getDailyParlays client"
```

---

### Task 6: RecordPanel nested drill-down — `web/app/builder/RecordPanel.tsx`

**Files:**
- Modify: `web/app/builder/RecordPanel.tsx`
- Modify: `web/app/builder/builder.module.css` (nested-row styles, reuse existing tokens)

**Interfaces:**
- Consumes: `getDailyParlays`, `DailyParlay` (T5).

- [ ] **Step 1: Add per-day parlay loading + nested render.** Convert each `daily` row into a click-to-expand control that lazy-loads `getDailyParlays(day.date)` into local state (keyed by date), renders a list of parlays (`{tier} {target_payout}x · {result} · {formatUnits(pnl)} · {stake}u`), each itself expandable to its legs. Leg row: `{label}` + `{actual} / {line}` + a glyph — `hit`/`won` → `✓`, `miss`/`lost` → `✗`, `void` → `–`, `null` → `·`. Result chips reuse the neutral W/L/P text styling (no signal-green). Guard: an in-flight/failed fetch shows a small "loading…"/"couldn't load" line. Keep `aria-expanded`/`aria-controls` on both expander levels (match the existing per-day toggle's a11y).

- [ ] **Step 2: CSS.** Add nested indentation + leg-row styles to `builder.module.css` using existing spacing/color CSS variables (no literal font-sizes off the DESIGN.md ramp — reuse an existing `recordMeta`/`dailyRow` size step).

- [ ] **Step 3: Type-check + build**

Run: `cd web && npx tsc --noEmit` → clean. `npx next build` → clean.

- [ ] **Step 4: Commit**

```bash
git add web/app/builder/RecordPanel.tsx web/app/builder/builder.module.css
git commit -m "feat(web): nested per-day -> parlay -> leg drill-down in RecordPanel"
```

---

## Architect execution steps (after tasks reviewed + merged)

1. `.venv/bin/python -m pytest -q` (full suite green).
2. `launchctl kickstart -k gui/$(id -u)/com.playstat.api` (api/main.py changed).
3. Live-check: `GET /parlay-builder/record/daily/parlays?date=2026-08-06` returns settled parlays with per-leg result (spot-check pid 296/297 — f5/NRFI hit/miss matches the DB).
4. Browser-verify the nested expanders render (dashboard is login-gated — run dev with `SESSION_SECRET=` empty to preview, per memory `dashboard-visual-preview-noauth`).
5. README §15.10 (or §15.9) note: additive drill-down endpoint + UI; Budgerr-unchanged.
6. `graphify update .`, then `git push`.

## Self-review notes

- **Coverage:** endpoint (T4), merge (T2), shaper (T3), schemas (T1), web client (T5), UI (T6). Settled-only enforced by joining `recommendation_outcomes` (only settled rows exist there). Honest framing in T6 (neutral chips).
- **Types:** row tuple `(parlay_id, result, cls, target_payout, combined_odds, stake, pnl, rec_legs, audit_legs)` consistent T3↔T4; `DailyParlayOut.legs: list[DailyParlayLegOut]` T1↔T2↔T3; TS mirrors Python (T5).
- **No placeholders:** T6 is prose (UI), not code-stubbed — acceptable per plan norms for presentational wiring; all data-shape contracts are fixed in T1–T5.
