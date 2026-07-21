# Parlay Builder Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `/builder` dashboard page (README §15.6, Stage 2) — a low-risk parlay constructor that server-renders the precomputed nightly parlays instantly and refines live on demand, with joint probability surfaced honestly as the headline number.

**Architecture:** Two additive API changes (an object-wrapped live search that carries a truncation flag; a fast read of the precomputed nightly rows that also becomes a Budgerr contract), then a Next 16 page that server-renders saved rows for first paint and fires an explicit server-side "Build" for live refinement. One shared `ConstructionList` renders saved and searched results identically.

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy (`api/`), pytest (`tests/`), Next.js **16.2.10** + React 19 (`web/`, App Router, server components), CSS Modules.

**Spec:** [`docs/superpowers/specs/2026-07-21-parlay-builder-dashboard-design.md`](../specs/2026-07-21-parlay-builder-dashboard-design.md). Read it before starting.

## Global Constraints

Every task's requirements implicitly include these. Values are verbatim from the spec and README §15.8.

- **Rank/filter only on de-vigged market probability. Never on `model_prob`.** `model_prob` is display context only.
- **No "+EV" / "edge" / "value" / "beat the market" language** anywhere — UI copy, component/file names, API payloads, comments, commit messages.
- **Joint probability is always the most prominent number on a construction card.** It is the risk.
- **Additive-only** to every existing contract endpoint. Do **not** modify `/edges`, `/box-scores`, `/games`, `/game-predictions`, `/parlay-recommendations`, or the `BuilderParlayOut` / `BuilderLegOut` / `ParlayLeg` schemas' existing fields.
- **Next.js is 16.2.10, not training-data Next.** Before writing any `web/` code, read the relevant guide in `web/node_modules/next/dist/docs/`. `middleware.ts` is deprecated in favour of `proxy.ts` (`web/AGENTS.md`). Do not guess the idiom.
- **DESIGN.md is binding**: near-black surface, tonal ladder (no shadows), Geist Mono + tabular figures for every number, Geist Sans for chrome/prose, 6px radii, no accent stripes, no KPI tiles. Signal Green is reserved — see Task 6.
- **The live API on `:8000` is production.** Test against a spare port (`8099`) with your own uvicorn and kill it after. Never restart or edit the launchd `com.playstat.api` service — the architect does that.
- **graphify-out/ is gitignored** and absent in worktrees; reading source directly is expected there. Do not burn turns trying to run graphify in a worktree.

## Reference — current shapes (already on main, `1f00735`)

`optimizer/builder_core.build(legs, target_payout=None, tolerance=DEFAULT_TOLERANCE, min_prob=None, min_legs=DEFAULT_MIN_LEGS, max_legs=DEFAULT_MAX_LEGS, top_n=10, max_nodes=MAX_NODES, stats=None)` returns `list[dict]`, each `{"legs": [legdict...], "combined_odds": float, "joint_prob": float, "n_legs": int}`. When a `stats` dict is passed it is populated with keys `nodes` (int), `truncated` (bool), `matches` (int), `candidate_games` (int).

Each `legdict` (from `builder_core.normalize_player_leg` / `normalize_team_leg`) has: `game_id`, `kind` (`"player"`/`"team"`), `label`, `side`, `decimal_odds`, `american_odds`, `market_prob`, `model_prob` (nullable), `line_value`, and — player only — `player_id`, `stat_type` (both `None`/absent-valued for team, where `market` is set instead).

Saved rows: `parlay_recommendations` columns `parlay_id, created_at, target_payout, legs (jsonb), joint_prob, combined_odds, kind`. Builder rows have `kind='builder'` and `legs = {"class": "across_game", "legs": [ {kind, game_id, player_id, stat_type, market, side, odds, line, label, market_prob, model_prob}, ... ]}`. Note the stored per-leg keys are `odds` (american int) and `line` (float) — already matching `BuilderLegOut`'s `odds`/`line` field names.

`api/schemas.py:180` `BuilderLegOut(game_id:int, kind:str, label:str, player_id:int|None, stat_type:str|None, market:str|None, side:str, line:float, odds:int, market_prob:float, model_prob:float|None)`.
`api/schemas.py:195` `BuilderParlayOut(legs:list[BuilderLegOut], combined_odds:float, joint_prob:float, n_legs:int)`.
`api/main.py:482` `GET /parlay-builder` currently returns `list[BuilderParlayOut]`.

---

### Task 1: `/parlay-builder` returns an object with a truncation flag (§6.1)

**Files:**
- Modify: `api/schemas.py` (add `BuilderSearchOut` after `BuilderParlayOut`, ~line 200)
- Modify: `api/main.py:482-536` (the `parlay_builder` endpoint)
- Test: `tests/test_parlay_builder_api.py` (create)

**Interfaces:**
- Produces: `BuilderSearchOut(constructions: list[BuilderParlayOut], truncated: bool, nodes_searched: int, exhaustive: bool)`. `exhaustive == not truncated` (kept as an explicit positive field for call-site clarity, per spec review). `GET /parlay-builder` now returns `BuilderSearchOut` (single object, not a list).

- [ ] **Step 1: Write the failing test**

The endpoint function is callable directly with a fake engine, following the pattern in `tests/test_parlay_recommendations_api.py`. Study that file first for the in-memory engine stand-in. The search itself is pure (`builder_core.build`), so the test drives the endpoint with a fake engine whose `load_legs` returns a fixed small leg set.

```python
# tests/test_parlay_builder_api.py
import api.main as main


def test_parlay_builder_returns_object_with_truncation_fields(monkeypatch):
    legs = [
        {"game_id": 1, "kind": "player", "label": "A over 0.5", "side": "over",
         "decimal_odds": 1.3, "american_odds": -333, "market_prob": 0.77,
         "model_prob": None, "line_value": 0.5, "player_id": 10, "stat_type": "hits", "market": None},
        {"game_id": 2, "kind": "player", "label": "B under 0.5", "side": "under",
         "decimal_odds": 1.25, "american_odds": -400, "market_prob": 0.80,
         "model_prob": 0.79, "line_value": 0.5, "player_id": 11, "stat_type": "runs", "market": None},
    ]
    monkeypatch.setattr(main.builder, "load_legs", lambda engine, floor: legs)

    out = main.parlay_builder(min_prob=0.5)

    assert out.constructions and out.constructions[0].n_legs == 2
    assert out.truncated is False
    assert out.exhaustive is True
    assert isinstance(out.nodes_searched, int) and out.nodes_searched > 0
    # No EV/edge field leaked into the payload.
    assert not hasattr(out.constructions[0], "ev")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_parlay_builder_api.py -v`
Expected: FAIL — `parlay_builder` returns a `list`, so `out.constructions` raises `AttributeError`.

- [ ] **Step 3: Add the schema**

In `api/schemas.py`, immediately after `BuilderParlayOut` (ends ~line 199):

```python
class BuilderSearchOut(BaseModel):
    constructions: list[BuilderParlayOut]
    # Whether the search hit its node budget and returned partial results.
    # exhaustive is the inverse, surfaced positively for call-site clarity.
    truncated: bool
    nodes_searched: int
    exhaustive: bool
```

- [ ] **Step 4: Rewrite the endpoint to capture stats and wrap the result**

In `api/main.py`, change the decorator's `response_model` and the body. Replace `response_model=list[BuilderParlayOut]` with `response_model=BuilderSearchOut`, and add `BuilderSearchOut` to the `from api.schemas import (...)` block. Replace the `legs`/`results`/`return` tail (currently lines 514-536) with:

```python
    legs = builder.load_legs(engine, floor)
    if not legs:
        return BuilderSearchOut(constructions=[], truncated=False, nodes_searched=0, exhaustive=True)
    stats: dict = {}
    results = builder_core.build(
        legs, target_payout=target_payout, tolerance=tolerance, min_prob=min_prob,
        min_legs=min_legs, max_legs=max_legs, top_n=top_n, stats=stats,
    )
    constructions = [
        BuilderParlayOut(
            legs=[
                BuilderLegOut(
                    game_id=leg["game_id"], kind=leg["kind"], label=leg["label"],
                    player_id=leg["player_id"], stat_type=leg["stat_type"],
                    market=leg["market"], side=leg["side"], line=leg["line_value"],
                    odds=leg["american_odds"], market_prob=leg["market_prob"],
                    model_prob=leg["model_prob"],
                )
                for leg in r["legs"]
            ],
            combined_odds=r["combined_odds"], joint_prob=r["joint_prob"], n_legs=r["n_legs"],
        )
        for r in results
    ]
    truncated = bool(stats.get("truncated", False))
    return BuilderSearchOut(
        constructions=constructions, truncated=truncated,
        nodes_searched=int(stats.get("nodes", 0)), exhaustive=not truncated,
    )
```

Update the endpoint docstring's first line to note the response is now an object `{constructions, truncated, nodes_searched, exhaustive}`. Keep the existing FLOOR/tolerance explanation.

- [ ] **Step 5: Run the test to verify it passes**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_parlay_builder_api.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite still green**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest -q`
Expected: all pass (195 + your new cases).

- [ ] **Step 7: Commit**

```bash
git add api/schemas.py api/main.py tests/test_parlay_builder_api.py
git commit -m "feat(api): /parlay-builder returns an object carrying a truncation flag

Truncation is a property of the search, not any one construction, so the
bare list had nowhere to report it. The dashboard needs it to tell the
user honestly when results are partial (README §15.6/§15.8). Additive: the
endpoint has no external consumer yet.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `GET /parlay-builder/saved` — precomputed nightly rows, a Budgerr contract (§6.2)

**Files:**
- Modify: `api/schemas.py` (add `SavedBuilderParlayOut` after `BuilderSearchOut`)
- Modify: `api/main.py` (add endpoint after `parlay_builder`, ~line 537)
- Modify: `README.md` §7.1 (add a note that this endpoint is a Budgerr contract surface)
- Test: `tests/test_parlay_builder_api.py` (extend)

**Interfaces:**
- Consumes: the `parlay_recommendations` table, `kind='builder'`.
- Produces: `SavedBuilderParlayOut(BuilderParlayOut)` adding `parlay_id: int`, `created_at: str`, `target_payout: float`. `GET /parlay-builder/saved?limit=10` returns `list[SavedBuilderParlayOut]`, most recent first.

- [ ] **Step 1: Write the failing test**

```python
def test_saved_builder_reads_only_builder_rows_and_unwraps_dict(monkeypatch):
    import api.main as main
    builder_row = (
        90, "2026-07-21 12:38:56-04", 2.0, 0.5416, 1.8224,
        {"class": "across_game", "legs": [
            {"kind": "player", "game_id": 1, "player_id": 10, "stat_type": "home_runs",
             "market": None, "side": "under", "odds": -1670, "line": 0.5,
             "label": "X home_runs under 0.5", "market_prob": 0.908, "model_prob": None},
            {"kind": "team", "game_id": 2, "player_id": None, "stat_type": None,
             "market": "first_inning_runs", "side": "under", "odds": -150, "line": 0.5,
             "label": "first_inning_runs under 0.5", "market_prob": 0.60, "model_prob": None},
        ]},
    )
    # Fake engine.begin() context manager returning a conn whose execute().fetchall()
    # yields builder_row — mirror the stand-in in tests/test_parlay_recommendations_api.py.
    monkeypatch.setattr(main, "engine", _fake_engine([builder_row]))

    out = main.saved_builder_parlays(limit=10)

    assert len(out) == 1
    assert out[0].parlay_id == 90 and out[0].target_payout == 2.0
    assert out[0].n_legs == 2
    team_leg = [l for l in out[0].legs if l.kind == "team"][0]
    assert team_leg.player_id is None and team_leg.market == "first_inning_runs"
```

Reuse / adapt the `_fake_engine` helper from `tests/test_parlay_recommendations_api.py` (import it or copy its shape). The SQL query must filter `WHERE kind = 'builder'`; assert that via the fake engine capturing the executed SQL text if that helper supports it, otherwise trust the query string in review.

- [ ] **Step 2: Run test to verify it fails**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_parlay_builder_api.py::test_saved_builder_reads_only_builder_rows_and_unwraps_dict -v`
Expected: FAIL — `saved_builder_parlays` does not exist.

- [ ] **Step 3: Add the schema**

```python
class SavedBuilderParlayOut(BuilderParlayOut):
    parlay_id: int
    created_at: str
    target_payout: float
```

- [ ] **Step 4: Add the endpoint**

In `api/main.py` after `parlay_builder`. Reuse the module-level `_as_legs_list` helper added in the merged Budgerr fix (it unwraps the `{"class","legs"}` dict). Add `SavedBuilderParlayOut` to the schema imports.

```python
@app.get("/parlay-builder/saved", response_model=list[SavedBuilderParlayOut])
def saved_builder_parlays(limit: int = 10):
    """The precomputed nightly low-risk builder parlays (kind='builder'), newest
    first. A fast list read (no live search) — this is the endpoint external
    consumers (Budgerr) should use, NOT the live /parlay-builder, which can take
    4-13s. Team legs (NRFI/F5) carry no team identity in `label`: they are
    game-level markets, so resolve the matchup via each leg's game_id -> /games.
    Ranked on de-vigged market probability; model_prob is context only.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT parlay_id, created_at, target_payout, joint_prob, combined_odds, legs
                FROM parlay_recommendations
                WHERE kind = 'builder'
                ORDER BY created_at DESC, joint_prob DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).fetchall()

    out = []
    for r in rows:
        legs_raw = _as_legs_list(r[5])
        out.append(
            SavedBuilderParlayOut(
                parlay_id=r[0], created_at=str(r[1]), target_payout=float(r[2]),
                joint_prob=float(r[3]), combined_odds=float(r[4]), n_legs=len(legs_raw),
                legs=[
                    BuilderLegOut(
                        game_id=leg["game_id"], kind=leg["kind"], label=leg["label"],
                        player_id=leg.get("player_id"), stat_type=leg.get("stat_type"),
                        market=leg.get("market"), side=leg["side"], line=leg["line"],
                        odds=leg["odds"], market_prob=leg["market_prob"],
                        model_prob=leg.get("model_prob"),
                    )
                    for leg in legs_raw
                ],
            )
        )
    return out
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `env -i PATH=/usr/bin:/bin HOME=$HOME /Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_parlay_builder_api.py -v`
Expected: PASS.

- [ ] **Step 6: Add the README contract note**

In `README.md` §7.1, append a paragraph:

```markdown
**`GET /parlay-builder/saved`** is also a Budgerr-facing contract surface (added
2026-07-21, Stage 2). It lists the precomputed nightly low-risk builder parlays
(`kind='builder'`) as a fast read, so Budgerr never has to call the slow live
`/parlay-builder` search on a user's critical path. Additive-only, like the
endpoints above. Its team legs (NRFI/F5) are game-level markets with no team in
`label` — resolve the matchup via `game_id` → `/games`.
```

- [ ] **Step 7: Verify live on spare port 8099**

Start your own uvicorn on 8099 (copy `.env` into the worktree first; `PLAYSTAT_API_KEYS` holds `name:key` pairs). Confirm:

```bash
/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m uvicorn api.main:app --port 8099 &
sleep 6
KEY=$(grep -m1 PLAYSTAT_API_KEYS .env | cut -d= -f2- | tr -d '"' | cut -d, -f1 | cut -d: -f2)
curl -s -H "X-API-Key: $KEY" "http://localhost:8099/parlay-builder/saved?limit=5" | python3 -m json.tool | head -30
```
Expected: HTTP 200, up to 5 objects each with `parlay_id`, `created_at`, `target_payout`, `joint_prob`, `combined_odds`, `n_legs`, `legs`. Kill the server after (`kill %1`).

- [ ] **Step 8: Commit**

```bash
git add api/schemas.py api/main.py tests/test_parlay_builder_api.py README.md
git commit -m "feat(api): GET /parlay-builder/saved — precomputed nightly parlays, Budgerr contract

Fast list read of the nightly kind='builder' rows so the dashboard first-
paints without a 4-13s live search and Budgerr has a robust off-critical-
path source (README §7.1/§15.6). Additive-only contract; team legs resolve
the matchup via game_id since NRFI/F5 carry no team in the label.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Frontend API client — types and reads (`web/app/lib/api.ts`)

**Files:**
- Modify: `web/app/lib/api.ts`

**Interfaces:**
- Produces (TypeScript): `BuilderLeg`, `BuilderConstruction`, `BuilderSearchResult`, `SavedBuilderParlay`, `BuilderSearchParams`; functions `getSavedBuilderParlays()`, `searchBuilder(params)`. All server-only (they use `apiGet`, which attaches the key server-side).

- [ ] **Step 1: Add the types**

Append to `web/app/lib/api.ts` after `BetPerformance`:

```typescript
export type BuilderLeg = {
  game_id: number;
  kind: "player" | "team";
  label: string;
  player_id: number | null;
  stat_type: string | null;
  market: string | null;
  side: "over" | "under";
  line: number;
  odds: number;
  market_prob: number;
  model_prob: number | null;
};

export type BuilderConstruction = {
  legs: BuilderLeg[];
  combined_odds: number;
  joint_prob: number;
  n_legs: number;
};

export type BuilderSearchResult = {
  constructions: BuilderConstruction[];
  truncated: boolean;
  nodes_searched: number;
  exhaustive: boolean;
};

export type SavedBuilderParlay = BuilderConstruction & {
  parlay_id: number;
  created_at: string;
  target_payout: number;
};

export type BuilderSearchParams = {
  target_payout?: number;
  min_prob?: number;
  max_legs?: number;
};
```

- [ ] **Step 2: Add the reads**

```typescript
export function getSavedBuilderParlays(limit = 10) {
  return apiGet<SavedBuilderParlay[]>(`/parlay-builder/saved?limit=${limit}`);
}

export function searchBuilder(params: BuilderSearchParams) {
  const q = new URLSearchParams();
  if (params.target_payout != null) q.set("target_payout", String(params.target_payout));
  if (params.min_prob != null) q.set("min_prob", String(params.min_prob));
  if (params.max_legs != null) q.set("max_legs", String(params.max_legs));
  return apiGet<BuilderSearchResult>(`/parlay-builder?${q.toString()}`);
}
```

- [ ] **Step 3: Typecheck**

Run: `cd web && npm run build`
Expected: build succeeds (types compile). If `node_modules` is missing (worktree), run `npm install` first.

- [ ] **Step 4: Commit**

```bash
git add web/app/lib/api.ts
git commit -m "feat(web): builder API client types and reads

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Live-search server route (`web/app/api/builder-search/route.ts`)

The Build button needs to trigger a live search, but `apiGet` (and the API key) are server-only. A Route Handler runs on the server, calls `searchBuilder`, and returns JSON to the client component.

**Files:**
- Create: `web/app/api/builder-search/route.ts`

**Interfaces:**
- Consumes: `searchBuilder(params)` from Task 3.
- Produces: `GET /api/builder-search?target_payout=&min_prob=&max_legs=` → `BuilderSearchResult` JSON (or `{ error }` with a non-200 status on failure). Same-origin; no API key crosses to the browser.

- [ ] **Step 1: Read the Next 16 route-handler guide**

Read `web/node_modules/next/dist/docs/` for the App Router route-handler API (the `GET(request)` export signature, how to read query params, and `Response`/`NextResponse` usage) **before writing**. Confirm the current idiom — do not assume the Next 13/14 shape.

- [ ] **Step 2: Write the handler**

Following the idiom you just confirmed, write a `GET` handler that parses `target_payout`, `min_prob`, `max_legs` from the request URL (numbers, optional), rejects the request with 422 if neither `target_payout` nor `min_prob` is present (mirroring the upstream), calls `searchBuilder`, and returns the result as JSON. On an upstream throw, return `{ error: "search failed" }` with status 502. Keep it small — it is a thin server-side proxy, the search stays in Python.

- [ ] **Step 3: Typecheck**

Run: `cd web && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Verify against the live API**

With the launchd `:8000` API running (the architect keeps it up), start the Next dev server via the preview tool (do NOT use bash for the dev server), then:
- `GET /api/builder-search?min_prob=0.75` returns a `BuilderSearchResult` with `constructions`.
- `GET /api/builder-search` (no params) returns 422.
Capture the JSON as proof.

- [ ] **Step 5: Commit**

```bash
git add web/app/api/builder-search/route.ts
git commit -m "feat(web): server route for live builder search, keeps API key server-side

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: The `/builder` page — server component, record strip, framing, ConstructionList

**Files:**
- Create: `web/app/builder/page.tsx`
- Create: `web/app/builder/ConstructionList.tsx`
- Create: `web/app/builder/RetryButton.tsx` (copy of `web/app/edges/RetryButton.tsx`)
- Create: `web/app/builder/builder.module.css`
- Modify: `web/app/page.tsx` (add a `/builder` link next to the existing `/edges` and `/clv` links)

**Interfaces:**
- Consumes: `getSavedBuilderParlays()`, `getBetPerformance()` (Task 3), `BuilderConstruction`, `SavedBuilderParlay`, `BetPerformance`.
- Produces: `ConstructionList({ constructions }: { constructions: BuilderConstruction[] })` — the shared renderer used here (saved rows) and in Task 6 (search results).

- [ ] **Step 1: Study the reference page**

Read `web/app/edges/page.tsx`, `web/app/edges/ParlaySection.tsx`, `web/app/edges/RetryButton.tsx`, and `web/app/edges/edges.module.css` for the established conventions (server component with `Promise.all`, `try/catch` → `fetchError` + `RetryButton`, `styles.*` module classes, `formatOdds`/`formatPercent` helpers). Read `DESIGN.md` fully.

- [ ] **Step 2: Write `ConstructionList.tsx`**

A presentational component taking `constructions: BuilderConstruction[]`. For each construction render a card where:
- **Joint probability is the largest element**: `≈ {(joint_prob*100).toFixed(1)}% to hit`, Geist Mono tabular, Display scale. Apply a `styles.safe` class (Signal Green) when `joint_prob >= 0.75`, else `styles.neutral` (Ink). (The 0.75 threshold and the DESIGN.md amendment are Task 6 — for now use the class names; the CSS lands in Step 3 here and colors finalize in Task 6.)
- Payout `{combined_odds.toFixed(2)}x` and `{n_legs} legs` beside it, Data role.
- Per leg: `label` + `side` in Geist Sans; `line`, `formatOdds(odds)`, and `market` prob `{(market_prob*100).toFixed(1)}%` in mono Ink; `model_prob` in Muted labelled `model — not used for ranking`, rendered as `—` when `null`.
Empty array → an empty-state message (no cards).

- [ ] **Step 3: Write `builder.module.css`**

Model it on `edges.module.css` (same tokens). Include `.root`, `.container`, `.back`, `.header`, `.title`, `.meta`, `.section`, card classes, `.safe`/`.neutral` for the joint-prob figure, leg-row classes, `.muted` for model_prob, and a `.framing` block for the honest copy. Use the DESIGN.md OKLCH tokens (or the existing CSS variables the other modules reference — check `web/app/globals.css`). No shadows, 6px radii.

- [ ] **Step 4: Write `page.tsx`**

Server component. `Promise.all([getSavedBuilderParlays(), getBetPerformance()])` in a `try/catch` → `fetchError` + `RetryButton` on failure (match `edges/page.tsx`). Render:
1. Back link + `<h1>Parlay Builder</h1>`.
2. **Record strip**: find the `parlay_builder` row in the `BetPerformance[]`. **If absent** (the normal state until the first settlement — the endpoint omits the key entirely), render `0-0-0 · n=0` with copy like "No settled builder parlays yet — the record starts once tonight's slate finishes." If present, render `{wins}-{losses}-{pushes} · ROI {(roi*100).toFixed(1)}% · n={n}`.
3. **Framing copy** (permanent, `styles.framing`): joint probability is the chance the whole parlay hits; a ~2x parlay is roughly a coin flip; each extra leg adds another vig bite, so at a fixed payout fewer legs is safer; ranking is on the book's de-vigged price, not our model; this is paper only. No "edge"/"value"/"+EV" wording.
4. A placeholder for the controls (`BuilderControls` lands in Task 6) — for this task, render `<ConstructionList constructions={saved} />` directly under a "Tonight's low-risk parlays" heading, where `saved` is the fetched saved rows. Empty saved rows → the spec's "not recorded yet" message.

- [ ] **Step 5: Add the nav link**

In `web/app/page.tsx`, add after the `/clv` link:
```tsx
<p style={{ marginTop: "0.5rem" }}>
  <Link href="/builder">Build a low-risk parlay &rarr;</Link>
</p>
```

- [ ] **Step 6: Typecheck + browser verify**

Run: `cd web && npm run build` → succeeds. Then via the preview tool, log in and open `/builder`. Confirm: the page first-paints with saved constructions (no spinner, no search), joint probability is the headline number, the record strip renders (likely the 0-0-0 empty state), and the framing copy is present. Screenshot as proof.

- [ ] **Step 7: Commit**

```bash
git add web/app/builder/ web/app/page.tsx
git commit -m "feat(web): /builder page — saved parlays, record strip, honest framing

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Controls, live search, truncation notice, and the DESIGN.md amendment

**Files:**
- Create: `web/app/builder/BuilderControls.tsx` (client component)
- Modify: `web/app/builder/page.tsx` (mount `BuilderControls`)
- Modify: `web/app/builder/builder.module.css` (controls, pending, amber truncation note)
- Modify: `DESIGN.md` §2 (one-line One-Signal-Rule amendment)

**Interfaces:**
- Consumes: `BuilderSearchResult`, `BuilderConstruction`, `ConstructionList`, `SavedBuilderParlay`. Calls `GET /api/builder-search` (Task 4) via `fetch` from the client.

- [ ] **Step 1: Amend DESIGN.md**

In `DESIGN.md` §2 under "The One Signal Rule", add one sentence:
> On pages that surface no edge (e.g. `/builder`), Signal Green marks *the one number the user came for* — on `/builder` that is joint probability, green only when a construction is genuinely low-risk (≥ 75% to hit). The rule's spirit — green is rare and meaningful, never decorative — is unchanged.

- [ ] **Step 2: Write `BuilderControls.tsx`**

`"use client"`. Props: `initial: SavedBuilderParlay[]` (the server-rendered saved rows, shown until the user searches). State: `mode: "payout" | "prob"`, `payout` (default 2.0), `prob` (default 0.75), `pending`, `result: BuilderSearchResult | null`, `error`.
- A two-way toggle: "I want a payout of…" / "I want a hit chance of at least…". Only the pinned control is an editable input; the other axis is shown as derived text ("… ≈ the builder finds the rest").
- **Build** button (the page's one primary action, Signal Green): disabled while `pending`; sets `pending`, `fetch`es `/api/builder-search` with `target_payout` (payout mode) or `min_prob` (prob mode), sets `result`, clears `pending`.
- Render: while `pending`, the spec's copy "Searching constructions — this takes a few seconds." When `result` is set: if `result.truncated`, an **Edge Amber** note (`styles.truncated`) — "Showing the best constructions found within the search budget; not proven optimal." Then `<ConstructionList constructions={result.constructions} />`. Empty `result.constructions` → the spec's no-results copy ("Nothing on tonight's slate reaches that floor — try relaxing it."). Before any search, show `<ConstructionList constructions={initial} />`.

- [ ] **Step 3: Mount it in `page.tsx`**

Replace Task 5's direct `<ConstructionList constructions={saved} />` with `<BuilderControls initial={saved} />`. Keep the record strip and framing copy above it.

- [ ] **Step 4: Style controls, pending, and the amber note**

In `builder.module.css`: toggle/input/button styling (button = Signal Green primary per DESIGN.md §5; inputs = Surface Raised + Border, focus → Signal Green border, no glow), a `.pending` block, and `.truncated` using Edge Amber (`oklch(0.75 0.15 70)`) — never combined with Signal Green in the same element. Finalize `.safe` = Signal Green, `.neutral` = Ink from Task 5.

- [ ] **Step 5: Typecheck + full browser verification**

Run: `cd web && npm run build` → succeeds. Via the preview tool, logged in on `/builder`:
1. First paint shows saved rows, no search.
2. Switch to "hit chance" mode, set 0.75, Build → pending copy appears, then results; a ≥75% construction shows its joint prob in Signal Green.
3. Switch to "payout" mode, set 2.0, Build → results at ~2.0x show joint prob in Ink (below 75%) and, on a full slate, the amber truncation note.
4. `resize_window` to mobile — layout holds, page does not scroll horizontally.
Screenshots of steps 2 and 3 as proof.

- [ ] **Step 6: Regression check**

Confirm `/edges` and `/clv` still render, and `GET /parlay-recommendations` still returns 200 at `limit=10` and `limit=50` (the merged fix — verify it wasn't disturbed).

- [ ] **Step 7: Commit**

```bash
git add web/app/builder/ DESIGN.md
git commit -m "feat(web): builder controls, live search, truncation notice; DESIGN.md one-signal amendment

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** §5 architecture → Tasks 3–6 (files match). §6.1 → Task 1. §6.2 → Task 2 (schema, endpoint, README note, team-leg docstring). §7 → out of scope here (separate plan). §8 → merged already; Task 6 Step 6 regression-checks it. §9 page composition → Tasks 5 (header, record strip, framing, cards) + 6 (controls, truncation). §10 states → Task 5 (empty saved, error) + Task 6 (pending, no-results, truncated). §11 design system → Task 6 Step 1 (DESIGN.md) + CSS across 5/6. §12 testing → pytest in 1/2, `npm run build` + browser in 3–6. §13 out of scope respected (no same-game, no model-prob ranking, no both-pinned UI).

**Placeholder scan:** Task 4 intentionally defers the exact route-handler code to "the idiom you confirmed" because the Next 16 API must be read from `node_modules` first — this is a deliberate read-before-write gate, not a vague instruction; the behavior (params, 422, JSON, 502) is fully specified. All Python steps carry complete code.

**Type consistency:** `BuilderSearchOut` (Python) ↔ `BuilderSearchResult` (TS) fields match: `constructions/truncated/nodes_searched/exhaustive`. `SavedBuilderParlayOut` ↔ `SavedBuilderParlay` both add `parlay_id/created_at/target_payout` to the construction shape. `BuilderLegOut` ↔ `BuilderLeg` fields match. `ConstructionList` takes `BuilderConstruction[]` in both call sites (saved rows widen it, which is assignable).
