# NFL Player-Prop Builder Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the sport-blind builder sport-aware so `optimizer.builder --sport nfl --save` builds/saves NFL across-game player-prop parlays that never mix with MLB.

**Architecture:** Add a `sport` parameter (default `"mlb"`) through `optimizer/builder.py`'s loaders + `save_builds` + CLI, filtering the games join by `g.sport` and stamping `sport` into the saved legs-blob wrapper. Add an additive `?sport` filter (default `"mlb"`) to `GET /parlay-builder/saved`. Settlement is already sport-agnostic — no change, just a proof test.

**Tech Stack:** Python 3.11, pytest, SQLAlchemy, pandas, FastAPI.

## Global Constraints

- **The DB is LIVE.** `ingestion.db.get_engine()` is the production database. NO test calls a real engine: DB-facing SQL is checked by `inspect.getsource` (per `tests/test_builder.py` / `tests/test_parlay_recommendations_api.py`), `save_builds`'s blob via the in-memory `_CapturingEngine`, and settlement via the pure scoring functions. Never write the live DB from a test.
- **`sport` default is `"mlb"` EVERYWHERE** (loaders, `save_builds`, CLI, endpoint). The MLB daily chain runs the builder with NO `--sport`, so its behavior must stay byte-identical.
- **Backward-compat via COALESCE.** Existing MLB builder rows have no `sport` key in their blob; readers treat an absent key as `"mlb"` (`COALESCE(legs->>'sport', 'mlb')`). This keeps Budgerr's no-`sport` call to `/parlay-builder/saved` returning exactly MLB (§7.1 — additive-only).
- **Inherited §15.8 guardrails are untouched** — this plan adds no builder-core/search/ranking change; it only routes by sport. Do not alter `builder_core.build`, `normalize_player_leg`, or any ranking/floor logic.
- **graphify:** `graphify-out/graph.json` exists in the MAIN checkout only (gitignored — absent in a worktree). Read the source files named below directly.
- **Worktree setup:** `cp /Users/aayushpokhrel/dev/playstat/.env ./.env`; run tests with `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest` from the worktree cwd.

---

## File Structure

- **Modify** `optimizer/builder.py`: `sport` param + `g.sport` filter on `load_player_legs`/`load_team_legs`/`load_legs`; `sport` param + blob field on `save_builds`; `--sport` CLI flag threaded to both.
- **Modify** `api/main.py`: `sport` query param + `COALESCE(legs->>'sport','mlb') = :sport` filter on `saved_builder_parlays`.
- **Modify (tests)** `tests/test_builder.py`, `tests/test_parlay_builder_api.py`, `tests/test_settle_builder.py`.

---

### Task 1: Sport-filter the loaders

**Files:**
- Modify: `optimizer/builder.py` (`load_player_legs` ~L27, `load_team_legs` ~L62, `load_legs` ~L101)
- Test: `tests/test_builder.py`

**Interfaces:**
- Produces: `load_player_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb")`, `load_team_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb")`, `load_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb")` — each filters the games join with `AND g.sport = :sport`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_builder.py` (after the slate-window tests):
```python
# --- sport filtering (NFL builder sub-project #2) ----------------------------

@pytest.mark.parametrize("fn", [builder.load_player_legs, builder.load_team_legs])
def test_loaders_have_sport_param_defaulting_to_mlb(fn):
    sig = inspect.signature(fn)
    assert "sport" in sig.parameters
    assert sig.parameters["sport"].default == "mlb"


@pytest.mark.parametrize("fn", [builder.load_player_legs, builder.load_team_legs])
def test_loaders_filter_games_join_by_sport(fn):
    source = inspect.getsource(fn)
    assert "g.sport = :sport" in source
    # slate + FT guards must remain alongside the new sport filter
    assert "g.date = COALESCE(:slate_date, CURRENT_DATE)" in source
    assert "g.status != 'FT'" in source


def test_load_legs_threads_sport_to_both_loaders():
    sig = inspect.signature(builder.load_legs)
    assert sig.parameters["sport"].default == "mlb"
    source = inspect.getsource(builder.load_legs)
    assert "load_player_legs(engine, floor, slate_date, sport)" in source
    assert "load_team_legs(engine, floor, slate_date, sport)" in source
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder.py -q -k sport`
Expected: FAIL — no `sport` parameter / `g.sport = :sport` not in source.

- [ ] **Step 3: Implement**

In `optimizer/builder.py`, edit `load_player_legs`: add `sport="mlb"` to the signature, add `AND g.sport = :sport` to the games join, and pass `sport` in params. The games join becomes:
```python
                JOIN games g ON g.game_id = pl.game_id AND g.status != 'FT'
                    AND g.date = COALESCE(:slate_date, CURRENT_DATE)
                    AND g.sport = :sport
```
and the params call:
```python
            conn, params={"slate_date": slate_date, "sport": sport},
```
Do the same for `load_team_legs` (its join is the same `JOIN games g ON g.game_id = gl.game_id AND g.status != 'FT' AND g.date = COALESCE(:slate_date, CURRENT_DATE)` → add `AND g.sport = :sport`; params become `{"markets": list(TEAM_MARKETS), "slate_date": slate_date, "sport": sport}`).
Update the signatures:
```python
def load_player_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb"):
def load_team_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb"):
```
And `load_legs`:
```python
def load_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb"):
    return (load_player_legs(engine, floor, slate_date, sport)
            + load_team_legs(engine, floor, slate_date, sport))
```

- [ ] **Step 4: Run to verify they pass**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder.py -q -k sport`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add optimizer/builder.py tests/test_builder.py
git commit -m "feat(builder): sport-filter candidate-leg loaders (NFL tier #2)"
```

---

### Task 2: Stamp `sport` into the saved legs blob

**Files:**
- Modify: `optimizer/builder.py` (`save_builds` ~L106)
- Test: `tests/test_builder.py`

**Interfaces:**
- Produces: `save_builds(engine, target_payout, results, parlay_class="across_game", sport="mlb")` — writes `{"class": parlay_class, "sport": sport, "legs": [...]}` into `parlay_recommendations.legs`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_builder.py`:
```python
def test_save_builds_stamps_sport_into_blob():
    engine = _CapturingEngine()
    builder.save_builds(engine, 2.0, _one_result("player"), sport="nfl")
    blob = json.loads(engine.calls[0]["legs"])
    assert blob["sport"] == "nfl"
    assert blob["class"] == "across_game"  # class still written as before


def test_save_builds_sport_defaults_to_mlb():
    engine = _CapturingEngine()
    builder.save_builds(engine, 1.4, _one_result("player"))
    blob = json.loads(engine.calls[0]["legs"])
    assert blob["sport"] == "mlb"
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder.py::test_save_builds_stamps_sport_into_blob tests/test_builder.py::test_save_builds_sport_defaults_to_mlb -q`
Expected: FAIL — `KeyError: 'sport'` on the blob.

- [ ] **Step 3: Implement**

In `optimizer/builder.py` `save_builds`, add `sport="mlb"` to the signature and add `"sport": sport` to the wrapper dict. The signature:
```python
def save_builds(engine, target_payout, results, parlay_class="across_game", sport="mlb"):
```
The `json.dumps` wrapper changes from `{"class": parlay_class, "legs": legs_json}` to:
```python
                    "legs": json.dumps({"class": parlay_class, "sport": sport,
                                        "legs": legs_json}, allow_nan=False),
```

- [ ] **Step 4: Run to verify they pass**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder.py -q -k save_builds`
Expected: PASS (incl. the two pre-existing `save_builds` class tests, unchanged).

- [ ] **Step 5: Commit**

```bash
git add optimizer/builder.py tests/test_builder.py
git commit -m "feat(builder): stamp sport into the saved legs blob (NFL tier #2)"
```

---

### Task 3: `--sport` CLI flag threaded to loaders + save

**Files:**
- Modify: `optimizer/builder.py` (`main` ~L151)
- Test: `tests/test_builder.py`

**Interfaces:**
- Consumes: the `sport`-aware `load_legs`/`load_team_legs`/`save_builds` from Tasks 1–2.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_builder.py`:
```python
def test_main_has_sport_flag_defaulting_to_mlb_and_threads_it():
    source = inspect.getsource(builder.main)
    assert '"--sport"' in source or "'--sport'" in source
    assert 'default="mlb"' in source
    # threaded into loading and saving
    assert "args.sport" in source
    assert "load_legs(engine, args.floor, args.slate_date, args.sport)" in source
    assert "load_team_legs(engine, args.floor, args.slate_date, args.sport)" in source
    assert ", args.sport)" in source  # save_builds call carries sport last
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder.py::test_main_has_sport_flag_defaulting_to_mlb_and_threads_it -q`
Expected: FAIL — no `--sport` in `main`.

- [ ] **Step 3: Implement**

In `optimizer/builder.py` `main`, add the argparse flag (next to `--slate-date`):
```python
    parser.add_argument("--sport", default="mlb",
                        help="which sport's candidate legs to build from "
                             "(default: mlb — the daily chain passes no --sport). "
                             "nfl builds player-only until the team tier lands (#3).")
```
Thread it into the load calls and the save call:
```python
    if args.team_only:
        legs = load_team_legs(engine, args.floor, args.slate_date, args.sport)
    else:
        legs = load_legs(engine, args.floor, args.slate_date, args.sport)
```
```python
    if args.save:
        parlay_class = "team_tier" if args.team_only else "across_game"
        saved = save_builds(engine, args.target_payout or 0.0, results, parlay_class, args.sport)
```

- [ ] **Step 4: Run to verify it passes + full builder file green**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder.py -q`
Expected: PASS (all, incl. pre-existing).

- [ ] **Step 5: Commit**

```bash
git add optimizer/builder.py tests/test_builder.py
git commit -m "feat(builder): --sport CLI flag (default mlb) for the NFL tier (#2)"
```

---

### Task 4: `?sport` filter on `GET /parlay-builder/saved`

**Files:**
- Modify: `api/main.py` (`saved_builder_parlays` ~L560)
- Test: `tests/test_parlay_builder_api.py`

**Interfaces:**
- Produces: `saved_builder_parlays(limit=10, tier="player", sport="mlb")` — filters `AND COALESCE(legs->>'sport', 'mlb') = :sport`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_parlay_builder_api.py`:
```python
def test_saved_builder_parlays_has_sport_param_defaulting_to_mlb():
    import inspect
    sig = inspect.signature(main.saved_builder_parlays)
    assert sig.parameters["sport"].default == "mlb"


def test_saved_builder_query_filters_by_sport_with_mlb_default_coalesce():
    import inspect
    source = inspect.getsource(main.saved_builder_parlays)
    assert "COALESCE(legs->>'sport', 'mlb') = :sport" in source
    assert '"sport": sport' in source
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_parlay_builder_api.py -q -k sport`
Expected: FAIL — no `sport` param / COALESCE clause.

- [ ] **Step 3: Implement**

In `api/main.py` `saved_builder_parlays`, add `sport: str = "mlb"` to the signature (after `tier`). Add the sport filter to the WHERE and the param. The query's fixed WHERE line becomes:
```python
                WHERE kind = 'builder'
                AND COALESCE(legs->>'sport', 'mlb') = :sport
```
(the existing `+ ("" if tier == "all" else "AND legs->>'class' = :cls ")` concatenation is unchanged, appended after). Add `"sport": sport` to the params dict:
```python
            {"limit": limit, "cls": TIER_TO_CLASS.get(tier), "sport": sport},
```

- [ ] **Step 4: Run to verify they pass + endpoint file green**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_parlay_builder_api.py -q`
Expected: PASS (all, incl. the pre-existing shaping/enrichment tests — the fixture queue they feed is unchanged since the new WHERE clause doesn't add a query).

- [ ] **Step 5: Commit**

```bash
git add api/main.py tests/test_parlay_builder_api.py
git commit -m "feat(api): additive ?sport filter on /parlay-builder/saved (NFL tier #2)"
```

---

### Task 5: Prove NFL player-parlay settlement (no code change)

**Files:**
- Test: `tests/test_settle_builder.py`

**Interfaces:**
- Consumes: existing `builder_leg_key`, `settle_leg`, `parlay_result`, `leg_status` (pure, sport-agnostic).

- [ ] **Step 1: Write the test**

Append to `tests/test_settle_builder.py`:
```python
# --- NFL player legs settle through the same sport-agnostic path (tier #2) ---
# settle_builder_parlays looks up player_game_stats[(player_id, game_id,
# stat_type)] and scores over/under vs the line -- nothing MLB-specific. These
# assert the pure scoring path handles NFL stat_types/lines identically.

def test_builder_leg_key_handles_an_nfl_player_leg():
    leg = {"kind": "player", "game_id": 200000123, "player_id": 200000045,
           "stat_type": "passing_yards", "market": None}
    assert builder_leg_key(leg) == ("player", 200000045, 200000123, "passing_yards")


def test_nfl_passing_yards_over_scores_by_the_same_rule():
    # 290 passing yards clears an over 274.5; 12 rushing yards clears an under 45.5
    assert settle_leg("over", 290.0, 274.5) == "hit"
    assert settle_leg("under", 12.0, 45.5) == "hit"
    assert settle_leg("over", 250.0, 274.5) == "miss"


def test_nfl_two_leg_parlay_all_hit_wins():
    results = [settle_leg("over", 290.0, 274.5), settle_leg("under", 3.0, 6.5)]
    assert results == ["hit", "hit"]
    result, _, pnl = parlay_result(results, [1.8, 1.9])
    assert result == "win"
    assert pnl == pytest.approx(1.8 * 1.9 - 1.0)


def test_nfl_dnp_receiver_leg_voids_like_any_other():
    # A receiver who didn't play -> FT game, no stat row -> void (dropped).
    assert leg_status("FT", None) == "void"
```

- [ ] **Step 2: Run to verify they pass (no implementation needed)**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_settle_builder.py -q`
Expected: PASS — these assert existing sport-agnostic behavior; no code change.

- [ ] **Step 3: Run the whole suite (no regressions)**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest -q`
Expected: PASS — 285 baseline + the new tests (Tasks 1–5) all green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_settle_builder.py
git commit -m "test(settle): NFL player parlays settle via the sport-agnostic path (#2)"
```

---

## Self-Review

**Spec coverage:**
- Sport-filter the loaders + `--sport` flag (default mlb) — Tasks 1, 3. ✓
- Stamp `sport` into the legs blob — Task 2. ✓
- Additive `?sport` filter on `/parlay-builder/saved` (COALESCE default mlb, Budgerr-safe) — Task 4. ✓
- Settlement unchanged, NFL proof test — Task 5. ✓
- Guardrails / `builder_core` untouched — no task modifies them (constraint). ✓
- Default behavior byte-identical for MLB — every `sport` default is `"mlb"`; the MLB chain passes no `--sport` (Task 3) and Budgerr passes no `sport` (Task 4). ✓

**Placeholder scan:** every code step shows full code / exact SQL; no TBDs. ✓

**Type consistency:** `sport="mlb"` default is identical across `load_player_legs`/`load_team_legs`/`load_legs`/`save_builds` (Tasks 1–2), the CLI threads `args.sport` in loader-then-`save_builds` order (Task 3), and the endpoint uses `sport: str = "mlb"` + `"sport": sport` param (Task 4). `load_legs(engine, floor, slate_date, sport)` positional order matches its definition. ✓

## Out of Scope (later sub-projects)
- NFL team-markets tier + game-market settlement + spread/moneyline ingestion & `game_lines` schema change (#3).
- NFL nightly chain + dashboard + `/parlay-builder/record*` sport-filtering (#4).
- Any prediction-model change (separate track).
