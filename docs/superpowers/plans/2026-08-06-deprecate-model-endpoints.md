# Deprecate the frozen model-serving endpoints (roadmap #3A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. NOTE: the architect is executing this inline (small, consumer-contract-sensitive surgical edits — cheaper to do than to delegate; see `~/.claude/CLAUDE.md` delegation rule). The plan is written to full detail anyway per repo discipline (README-as-memory).

**Goal:** Wind down the three FROZEN model-serving endpoints — `GET /edges`, `GET /game-predictions`, `GET /parlay-recommendations` — via a phased, reversible deprecation, now that the model is shelved (§16, 2026-07-29) and their only external consumer (Budgerr) has migrated onto `/parlay-builder/saved` and acked.

**Architecture:** All three phases execute THIS session (user-confirmed 2026-08-06: "do all three phases now"), reaching the removed end state (404). Landed as a 2-commit trail for clean revertible checkpoints: **Commit A (soft-deprecate)** = `Deprecation`/`Sunset`/`Link` headers + bodies return `[]` (routes present, advertised, serving no data — reverting this restores frozen serving); **Commit B (remove)** = delete the three decorators/handlers → 404 + dead-code cleanup (the now-unused `_as_legs_list` api helper, `_mark_deprecated`, and any endpoint-only schema imports). The Sunset header (2026-08-20) is retained on the soft-deprecated checkpoint as a formal record even though removal follows immediately, since Budgerr already migrated. Commit B pulls forward the endpoint-only slice of roadmap #3(B); the broader model-code/table keep-vs-delete decision remains deferred.

**Tech Stack:** FastAPI (route handlers in `api/main.py`), Pydantic response models (`api/schemas.py`), pytest (DB-free fake-engine harness, `tests/test_parlay_recommendations_api.py`).

## Global Constraints

- **Additive-until-go, per the architect brief.** Phase 1 is additive. Phases 2–3 are breaking and require the user's explicit sign-off (obtained 2026-08-06: Budgerr migrated & acked; user endorsed proceeding through the breaking phases).
- **Budgerr contract surface (README §7.1).** `/edges`, `/game-predictions`, `/parlay-recommendations` are named Budgerr-facing endpoints. The migration was confirmed complete 2026-08-06. Forward source is `/parlay-builder/saved` (`?tier=all`, `?sport`).
- **Reserved lanes (architect only):** `git push`; `launchctl kickstart -k gui/$(id -u)/com.playstat.api` after ANY change to an API-imported module (`api/main.py` IS API-imported — every phase needs a kickstart); live browser/HTTP verification.
- **Every landed change updates the relevant README section (§7.1 + §16, and §11 for the deferred Phase 3) in the SAME commit and pushes.**
- **Guardrails (§15.8) unaffected** — this touches only the shelved model surfaces, never the builder.
- **DO NOT touch** the shared helpers `player_side`, `_resolve_leg_teams`, `_load_builder_team_context`, AND `_as_legs_list` in `api/main.py` — all four serve `/parlay-builder` and/or `/parlay-builder/saved`, NOT only the deprecated endpoints. VERIFIED 2026-08-06: `_as_legs_list` is called at both `/parlay-recommendations` (removed) AND `/parlay-builder/saved` (api/main.py:710) — it stays, and its `_as_legs_list` unit tests in `test_parlay_recommendations_api.py` stay.
- **`/edge-distributions`, `/model-performance`, `/players/{id}/predictions`, `/backtest-history`** are model-reading but OUT OF SCOPE for #3(A) (not in the architect's named list, and `/edge-distributions`/`/model-performance`/`/backtest-history` are not Budgerr contract surfaces). They belong to the #3(B) model-code/table cleanup decision.

---

## File Structure

- `api/main.py` — the three route handlers (`list_edges` L256, `game_predictions` L365, `list_parlay_recommendations` L507) and the endpoint-only helper `_as_legs_list` (L409). Modify per phase.
- `tests/test_parlay_recommendations_api.py` — endpoint regression tests. Update the two end-to-end tests in Phase 2 (empty body); keep the `_as_legs_list` unit tests through Phase 2 (helper still present), remove in Phase 3 with the helper.
- `README.md` — §7.1 (Budgerr contract), §16 (shelving section), §11 (deferred Phase 3 follow-up). Update in the same commit as each phase.

---

## Task 1 (Phase 1): Add Deprecation/Sunset/Link headers — ADDITIVE, non-breaking

**Files:**
- Modify: `api/main.py` (imports; `list_edges`, `game_predictions`, `list_parlay_recommendations`)
- Modify: `README.md` (§7.1, §16)

**Interfaces:**
- Consumes: FastAPI's `Response` injection — a handler that declares a `response: Response` parameter can mutate `response.headers` while still returning its `response_model` body; FastAPI merges the headers.
- Produces: three endpoints that return their existing frozen-row bodies UNCHANGED, plus headers `Deprecation: true`, `Sunset: Wed, 20 Aug 2026 00:00:00 GMT`, and `Link: </parlay-builder/saved>; rel="successor-version"`.

- [ ] **Step 1: Add the shared header constant + `Response` import**

In `api/main.py`, add `Response` to the fastapi import (`from fastapi import Depends, FastAPI, HTTPException, Response`). Near the top after `engine = get_engine()`, add:

```python
# --- Deprecation of the frozen model-serving endpoints (README §16 / §7.1) ---
# /edges, /game-predictions, /parlay-recommendations serve model rows frozen
# since the 2026-07-29 model shelving and have no live consumer (Budgerr
# migrated onto /parlay-builder/saved, confirmed 2026-08-06). Phased wind-down:
# headers now (additive), empty bodies next, route removal at the Sunset date.
_DEPRECATION_SUNSET = "Wed, 20 Aug 2026 00:00:00 GMT"


def _mark_deprecated(response: Response) -> None:
    """Stamp RFC 8594 / RFC 9745 deprecation headers on a response and point
    consumers at the forward source. Called by the three shelved model
    endpoints; no-op-safe on any Response."""
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = _DEPRECATION_SUNSET
    response.headers["Link"] = '</parlay-builder/saved>; rel="successor-version"'
```

- [ ] **Step 2: Stamp the headers in each of the three handlers**

`list_edges`: change signature to `def list_edges(response: Response):` and add `_mark_deprecated(response)` as the first line. Body otherwise unchanged.

`game_predictions`: change signature to `def game_predictions(response: Response, date: date_type | None = None, sport: str | None = None):` (Response first — it is a special non-query param; the `date`/`sport` query params keep their defaults and behavior). Add `_mark_deprecated(response)` as the first line.

`list_parlay_recommendations`: change signature to `def list_parlay_recommendations(response: Response, limit: int = 10):` and add `_mark_deprecated(response)` as the first line.

- [ ] **Step 3: Run the full test suite (no behavior change expected)**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest -q`
Expected: PASS (391 green — bodies unchanged; injecting `Response` does not alter the returned lists). The existing `test_parlay_recommendations_api.py` end-to-end tests still pass because they call the function with `limit=10` and now must pass a `response` — see Step 4.

- [ ] **Step 4: Fix the two direct-call tests to pass a Response stand-in**

The two end-to-end tests call `api_main.list_parlay_recommendations(limit=10)` directly. Adding the `response` param makes that a `TypeError`. Add a tiny stand-in and thread it through both calls:

```python
class _FakeResponse:
    """Stand-in for fastapi.Response — only .headers (a dict) is exercised."""
    def __init__(self):
        self.headers = {}
```

In `test_endpoint_survives_dormant_team_shape_and_serves_legacy_rows` and `test_endpoint_unchanged_shape_for_player_only_rows`, change the call to:
`results = api_main.list_parlay_recommendations(_FakeResponse(), limit=10)`
Add one assertion in each that the deprecation header was stamped, e.g.:
```python
resp = _FakeResponse()
results = api_main.list_parlay_recommendations(resp, limit=10)
assert resp.headers["Deprecation"] == "true"
```

- [ ] **Step 5: Run tests again**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest -q tests/test_parlay_recommendations_api.py`
Expected: PASS.

- [ ] **Step 6: Update README §7.1 and §16**

§7.1: add a note that `/edges`, `/game-predictions`, `/parlay-recommendations` are DEPRECATED (Sunset 2026-08-20), Budgerr migrated onto `/parlay-builder/saved` (confirmed 2026-08-06), headers now advertise the deprecation.
§16: under the shelving section, add a "Deprecation — Phase 1 (headers) DONE 2026-08-06" bullet with the Sunset date and the phased plan.

- [ ] **Step 7: Commit**

```bash
git add api/main.py tests/test_parlay_recommendations_api.py README.md docs/superpowers/plans/2026-08-06-deprecate-model-endpoints.md
git commit -m "feat(api): deprecate frozen model endpoints — Phase 1 Deprecation/Sunset headers (§7.1/§16)"
```

- [ ] **Step 8 (architect reserved): kickstart + live-verify + push**

```bash
launchctl kickstart -k gui/$(id -u)/com.playstat.api
```
Verify (with API key if `AUTH_ENABLED`): `curl -sD - -o /dev/null http://localhost:8000/edges | grep -iE "deprecation|sunset|link"` shows all three headers; `curl -s http://localhost:8000/edges | head -c 200` still returns frozen rows (body unchanged). Repeat for `/game-predictions` and `/parlay-recommendations`. Then `git push`.

---

## Task 2 (Phase 2): Empty bodies — BREAKING (user-signed-off), reversible

**Files:**
- Modify: `api/main.py` (three handlers)
- Modify: `tests/test_parlay_recommendations_api.py`
- Modify: `README.md` (§7.1, §16)

**Interfaces:**
- Consumes: the `_mark_deprecated` helper and headers from Task 1 (kept).
- Produces: the three endpoints return `[]` with a 200 and the deprecation headers; they no longer query the DB.

- [ ] **Step 1: Gut each handler body to return `[]` (keep the header stamp)**

`list_edges`:
```python
def list_edges(response: Response):
    """DEPRECATED (README §16, Sunset 2026-08-20). The model is shelved; this
    served rows frozen since 2026-07-29. Now returns []. Forward source:
    /parlay-builder/saved."""
    _mark_deprecated(response)
    return []
```
`game_predictions` (keep the `date`/`sport` params for signature compatibility so any lingering caller's query string still parses):
```python
def game_predictions(response: Response, date: date_type | None = None, sport: str | None = None):
    """DEPRECATED (README §16, Sunset 2026-08-20). Returns []. Forward source:
    /parlay-builder/saved."""
    _mark_deprecated(response)
    return []
```
`list_parlay_recommendations` (keep `limit` for signature compatibility):
```python
def list_parlay_recommendations(response: Response, limit: int = 10):
    """DEPRECATED (README §16, Sunset 2026-08-20). Returns []. Forward source:
    /parlay-builder/saved (?tier=all)."""
    _mark_deprecated(response)
    return []
```

- [ ] **Step 2: Update the endpoint tests to assert the deprecated empty behavior**

In `tests/test_parlay_recommendations_api.py`, replace the two end-to-end tests' assertions so they assert `== []` and that the header is stamped, WITHOUT needing the fake engine (the endpoint no longer queries). Keep the `_as_legs_list` unit tests (Steps `test_as_legs_list_*`) — the helper is still present until Phase 3. Keep `test_query_restricts_to_legacy_kinds_and_excludes_builder`? NO — the SQL is gone; remove that test (its guarded invariant no longer exists) and note why in the module docstring.

Example replacement:
```python
def test_endpoint_deprecated_returns_empty(monkeypatch):
    """Phase 2 deprecation (README §16): the endpoint no longer queries and
    returns [] with a Deprecation header, regardless of DB contents."""
    resp = _FakeResponse()
    # engine must NOT be touched; a poisoned engine proves no query is issued.
    monkeypatch.setattr(api_main, "engine", object())
    assert api_main.list_parlay_recommendations(resp, limit=10) == []
    assert resp.headers["Deprecation"] == "true"
    assert resp.headers["Sunset"] == api_main._DEPRECATION_SUNSET
```

- [ ] **Step 3: Run the full suite**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 4: Update README §7.1 and §16 — Phase 2 (empty) DONE**

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_parlay_recommendations_api.py README.md
git commit -m "feat(api): deprecate frozen model endpoints — Phase 2 empty bodies (§7.1/§16)"
```

- [ ] **Step 6 (architect reserved): kickstart + live-verify + push**

```bash
launchctl kickstart -k gui/$(id -u)/com.playstat.api
```
Verify: `curl -s http://localhost:8000/edges` → `[]`; same for `/game-predictions`, `/parlay-recommendations`; headers still present; HTTP still 200. Then `git push`.

---

## Task 3 (Phase 3 = Commit B): Remove routes + dead-code cleanup — EXECUTED THIS SESSION

Executed now (not deferred). Pulls forward the endpoint-only slice of #3(B); the broader model-code/table decision stays deferred.

**Files:** `api/main.py` (remove the three `@app.get` decorators + handler bodies; remove `_mark_deprecated` + `_DEPRECATION_SUNSET` (used only by the removed routes) and the now-unused `Response` import; drop now-unused schema imports `EdgeOut`, `GamePredictionOut`, `ParlayRecommendationOut`, `ParlayLeg` — VERIFIED 2026-08-06 each is used ONLY by its removed endpoint in `api/main.py`; the classes stay in `api/schemas.py`). **KEEP `_as_legs_list` (still used by `/parlay-builder/saved`, :710) and its unit tests.** `tests/test_parlay_recommendations_api.py`: remove the two end-to-end `/parlay-recommendations` tests + the SQL-guard test; keep the four `_as_legs_list` unit tests; update the module docstring. `README.md` §7.1/§11/§16.

**Steps:**
- [ ] (Import safety already verified 2026-08-06: `EdgeOut`/`GamePredictionOut`/`ParlayRecommendationOut`/`ParlayLeg` are each endpoint-only in `api/main.py`; `_as_legs_list` is shared and stays.)
- [ ] Remove the three decorators + handlers + `_mark_deprecated`/`_DEPRECATION_SUNSET` + the unused imports (`Response`, the four schemas).
- [ ] Prune the endpoint-specific tests (keep `_as_legs_list` units); run full suite.
- [ ] README §7.1 (drop the three from the contract list, note removal + Budgerr forward source), §16 (deprecation EXECUTED), §11 (note the endpoint removal; the model-code/table (B) decision still open).
- [ ] Commit; kickstart; verify all three now 404 AND `/parlay-builder/saved` + `/games` still 200; push.

---

## Self-Review

- **Spec coverage:** #3(A) = deprecate the three named endpoints, phased (headers → empty → remove), Budgerr-coordinated, doc updates in-commit. Tasks 1–3 cover it. Budgerr migration confirmed (user, 2026-08-06). ✅
- **Out-of-scope guarded:** `/edge-distributions`, `/model-performance`, `/players/{id}/predictions`, `/backtest-history` explicitly deferred to #3(B); shared builder helpers explicitly protected. ✅
- **Reversibility:** Phase 1 additive; Phase 2 = one-line body revert restores frozen serving; Phase 3 deferred + reversible via git. ✅
- **Reserved lanes:** every phase flags kickstart + push + live verify as architect-only. ✅
