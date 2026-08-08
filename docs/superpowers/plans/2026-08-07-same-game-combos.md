# Same-Game Combos (NRFI + F5) Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. This
> plan is executed by the Playstat architect via `~/.claude/bin/delegate` workers
> (which CANNOT run shell or commit) + architect review/pytest/commit. Each task is
> self-contained. **graphify query before reading source (or read directly in a
> worktree — the graph is gitignored there).**

**Goal:** Add a separate, labelled same-game NRFI+F5 parlay class that corrects the
joint probability by an empirically-measured correlation lift, and is honest about
the true joint (the risk), the non-placeable product payout, and the sample size.

**Architecture:** Re-source team legs from `game_lines` via the existing
`optimizer/builder.py:load_team_legs` (NOT the dropped `game_edges`). A pure
`same_game_pairs()` in `builder_core.py` groups those legs into one NRFI+F5 card per
game, lift-adjusting the joint. Persisted as `class="same_game_pair"` with lift/n
metadata in the JSONB wrapper. Surfaced via an additive `?tier=same_game` and a new
dashboard section. Settlement/record/staking need no scoring changes.

**Tech Stack:** Python 3 (pandas, SQLAlchemy, FastAPI, Pydantic), pytest, Next.js
16.x (React server + client components), Postgres.

## Global Constraints

- **Guardrails (§15.8, binding):** rank on devig `market_prob`; each leg
  `market_prob ≥ 0.55`; 2 legs; paper-only; **no +EV / edge / value / green
  language**; surface joint probability prominently (it is the risk).
- **Additive-only + mlb-default:** `/parlay-builder/saved` (default `tier=player`),
  `/box-scores`, `/games` response shapes byte-unchanged. Every new field/param
  optional/defaulted. Budgerr-safe. **No DB migration.**
- **No live DB in tests:** `ingestion.db.get_engine()` is LIVE. New tests are pure or
  use the fake-engine pattern (`tests/test_builder.py:_CapturingEngine`,
  `tests/test_settle_builder.py`, `tests/test_builder_record_api.py`).
- **Venv interpreter:** `/Users/aayushpokhrel/dev/playstat/.venv/bin/python`. Run
  pytest as `.venv/bin/python -m pytest`.
- **API-imported modules** (`optimizer/builder.py`, `optimizer/builder_core.py`,
  `api/main.py`) require an architect `launchctl kickstart` after landing — NOT a
  worker step. `modeling/correlation.py` is imported by `builder.py` → also
  kickstart-relevant. `scripts/daily_chain.sh` is NOT API-imported.
- **web/ rules:** read `PRODUCT.md` + `DESIGN.md` (near-black terminal surface, one
  signal-green accent reserved for the ≥75% joint-prob rule, Geist Sans/Mono) and
  `web/node_modules/next/dist/docs/` before writing Next code. Match
  `web/app/builder/` conventions.

---

### Task 1: Fix `nrfi_f5_lift` to measure at real lines + return sample cells

**Files:**
- Modify: `modeling/correlation.py` (rewrite `nrfi_f5_lift`; keep `empirical_lift`,
  `pair_joint_prob`, `_game_totals` untouched)
- Test: `tests/test_correlation.py`

**Interfaces:**
- Consumes: `empirical_lift(both, a, b, n)`, `_game_totals(engine, f5_line)` (existing).
- Produces: `nrfi_f5_lift(engine, side_nrfi, side_f5, nrfi_line, f5_line) -> (lift:
  float, n_games: int, both_n: int)`. `side_*` ∈ {"under","over"}; lines are floats.

**Why:** the dormant signature hardcodes `nrfi_line=1.5, f5_line=4.5` — the real
market NRFI line is 0.5 and the F5 line varies per game. Measuring the dependence at
the wrong line measures the wrong event. Callers now pass the pair's actual lines,
and the fn also returns the games count + the joint "both hit" cell for gating.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_correlation.py` (create the file if absent; keep any existing
tests). This uses a tiny fake engine returning a fixed 4-game frame so no live DB is
touched — `_game_totals` calls `pd.read_sql(text(...), conn)`, so the fake `conn`
just needs to make `pd.read_sql` yield our frame. Simplest: monkeypatch
`_game_totals`.

```python
import modeling.correlation as corr


def test_nrfi_f5_lift_reads_given_lines_and_returns_cells(monkeypatch):
    import pandas as pd
    # 4 games: (fi, f5) totals. under 0.5 fi => fi==0; under 4.5 f5 => f5<4.5
    frame = pd.DataFrame(
        {"game_id": [1, 2, 3, 4], "fi": [0, 0, 2, 3], "f5": [3, 6, 2, 8]}
    )
    monkeypatch.setattr(corr, "_game_totals", lambda engine, f5_line: frame)
    lift, n, both = corr.nrfi_f5_lift(
        object(), "under", "under", nrfi_line=0.5, f5_line=4.5
    )
    # under/under: fi<0.5 -> games 1,2 (a=2); f5<4.5 -> games 1,3 (b=2); both -> game 1 (=1)
    # expected = (2/4)*(2/4)=0.25 ; observed = 1/4=0.25 ; lift=1.0
    assert n == 4
    assert both == 1
    assert lift == 1.0


def test_nrfi_f5_lift_over_side(monkeypatch):
    import pandas as pd
    frame = pd.DataFrame(
        {"game_id": [1, 2, 3, 4], "fi": [0, 0, 2, 3], "f5": [3, 6, 2, 8]}
    )
    monkeypatch.setattr(corr, "_game_totals", lambda engine, f5_line: frame)
    lift, n, both = corr.nrfi_f5_lift(
        object(), "over", "over", nrfi_line=0.5, f5_line=4.5
    )
    # over/over: fi>0.5 -> games 3,4 (a=2); f5>4.5 -> games 2,4 (b=2); both -> game 4 (=1)
    assert (n, both) == (4, 1)
    assert lift == 1.0


def test_empirical_lift_empty_marginal_returns_one():
    assert corr.empirical_lift(0, 0, 5, 10) == 1.0
    assert corr.empirical_lift(0, 5, 0, 10) == 1.0
    assert corr.empirical_lift(1, 2, 3, 0) == 1.0


def test_pair_joint_prob_clamps_to_min_marginal():
    # huge lift can't push joint above the smaller marginal
    assert corr.pair_joint_prob(0.6, 0.55, 10.0) == 0.55
    # negative-direction lift lowers the joint below the product
    assert corr.pair_joint_prob(0.6, 0.6, 0.5) == 0.6 * 0.6 * 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_correlation.py -v`
Expected: FAIL — `nrfi_f5_lift() takes ... positional arguments` / signature/return
mismatch (old returns `(lift, n)`, no `both`).

- [ ] **Step 3: Rewrite `nrfi_f5_lift`**

Replace the existing `nrfi_f5_lift` in `modeling/correlation.py` with:

```python
def nrfi_f5_lift(engine, side_nrfi, side_f5, nrfi_line, f5_line):
    """(lift, n_games, both_n) for a same-game NRFI-side x F5-side pair, measured
    at the pair's ACTUAL market lines (fi line, that game's f5 line) over all
    box-score history. both_n is the joint "both hit" cell count (sample-gating).
    """
    df = _game_totals(engine, f5_line)
    n = len(df)
    nrfi_hit = (df["fi"] < nrfi_line) if side_nrfi == "under" else (df["fi"] > nrfi_line)
    f5_hit = (df["f5"] < f5_line) if side_f5 == "under" else (df["f5"] > f5_line)
    both = int((nrfi_hit & f5_hit).sum())
    return empirical_lift(both, int(nrfi_hit.sum()), int(f5_hit.sum()), n), n, both
```

Also update the module docstring's `nrfi_line=1.5` mention if present (it isn't — the
top docstring is generic). Leave `empirical_lift`, `pair_joint_prob`, `_game_totals`
exactly as-is.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_correlation.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add modeling/correlation.py tests/test_correlation.py
git commit -m "feat(correlation): nrfi_f5_lift measures at real lines, returns (lift,n,both_n)"
```

---

### Task 2: Pure `same_game_pairs()` in `builder_core.py` (with sample gate)

**Files:**
- Modify: `optimizer/builder_core.py` (add `same_game_pairs`; import
  `pair_joint_prob` from `modeling.correlation`)
- Test: `tests/test_builder_core.py`

**Interfaces:**
- Consumes: builder-normalized team legs (dicts from `normalize_team_leg`, each with
  `game_id, market, side, market_prob, decimal_odds, american_odds, line_value,
  label, book, kind="team"`); `modeling.correlation.pair_joint_prob`.
- Produces:
  `same_game_pairs(team_legs, lift_fn, top_n=10, min_games=500, min_both=50,
  warn_below=2000) -> list[dict]`. Each dict:
  `{"legs": [nrfi_leg, f5_leg], "combined_odds": float, "joint_prob": float,
  "lift": float, "lift_n": int, "both_n": int, "small_sample": bool,
  "n_legs": 2}`. `lift_fn(side_nrfi, side_f5, nrfi_line, f5_line) -> (lift, n_games,
  both_n)` is injected (keeps this DB-free).

**Design notes:** one card per game that has BOTH a `first_inning_runs` leg and an
`f5_runs` leg (both already floor-passing — the caller loads floor-filtered legs).
The card's two legs are each already the market's favorite side. Gate: drop a card
whose `lift_n < min_games` or `both_n < min_both`. Flag `small_sample = lift_n <
warn_below`. Rank surviving cards by lift-adjusted `joint_prob` desc, return top-N.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_builder_core.py`:

```python
from optimizer.builder_core import same_game_pairs


def _tleg(game_id, market, side, prob, dec, line=0.5):
    return {
        "game_id": game_id, "market": market, "side": side, "market_prob": prob,
        "decimal_odds": dec, "american_odds": -120, "line_value": line,
        "label": f"{market} {side} {line}", "book": None, "kind": "team",
    }


def _lift_fn_stub(lift=1.30, n=2000, both=1000):
    return lambda sn, sf, nl, fl: (lift, n, both)


def test_same_game_pairs_one_card_per_game_with_both_markets():
    legs = [
        _tleg(1, "first_inning_runs", "under", 0.56, 1.8, 0.5),
        _tleg(1, "f5_runs", "under", 0.57, 1.9, 4.5),
        _tleg(2, "first_inning_runs", "under", 0.55, 1.7, 0.5),  # game 2 missing f5
    ]
    cards = same_game_pairs(legs, _lift_fn_stub(), top_n=10)
    assert len(cards) == 1
    c = cards[0]
    assert c["n_legs"] == 2
    assert c["combined_odds"] == 1.8 * 1.9
    assert c["lift"] == 1.30 and c["lift_n"] == 2000 and c["both_n"] == 1000
    # joint = 0.56*0.57*1.30 clamped to <= min(0.56,0.57)
    assert abs(c["joint_prob"] - 0.56 * 0.57 * 1.30) < 1e-9
    assert c["small_sample"] is False


def test_same_game_pairs_gates_low_sample():
    legs = [
        _tleg(1, "first_inning_runs", "under", 0.56, 1.8),
        _tleg(1, "f5_runs", "under", 0.57, 1.9, 4.5),
    ]
    # both_n below floor -> dropped entirely
    assert same_game_pairs(legs, _lift_fn_stub(n=1000, both=40), min_both=50) == []
    # n_games below floor -> dropped
    assert same_game_pairs(legs, _lift_fn_stub(n=400, both=300), min_games=500) == []


def test_same_game_pairs_flags_small_sample_but_still_shows():
    legs = [
        _tleg(1, "first_inning_runs", "under", 0.56, 1.8),
        _tleg(1, "f5_runs", "under", 0.57, 1.9, 4.5),
    ]
    cards = same_game_pairs(legs, _lift_fn_stub(n=1500, both=700), warn_below=2000)
    assert len(cards) == 1 and cards[0]["small_sample"] is True


def test_same_game_pairs_ranks_by_joint_and_caps_top_n():
    legs = []
    for g, (p, dec) in enumerate([(0.56, 1.8), (0.60, 1.8), (0.58, 1.8)], start=1):
        legs.append(_tleg(g, "first_inning_runs", "under", p, dec))
        legs.append(_tleg(g, "f5_runs", "under", p, dec, 4.5))
    cards = same_game_pairs(legs, _lift_fn_stub(), top_n=2)
    assert len(cards) == 2
    # highest joint first: game 2 (0.60) then game 3 (0.58)
    assert cards[0]["legs"][0]["game_id"] == 2
    assert cards[1]["legs"][0]["game_id"] == 3


def test_same_game_pairs_empty_input():
    assert same_game_pairs([], _lift_fn_stub()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_builder_core.py -k same_game -v`
Expected: FAIL — `cannot import name 'same_game_pairs'`.

- [ ] **Step 3: Implement `same_game_pairs`**

Add near the other pure helpers in `optimizer/builder_core.py`. Add the import at
the top of the file (after the existing `from optimizer.parlay import
american_to_decimal`):

```python
from modeling.correlation import pair_joint_prob
```

Then add:

```python
def same_game_pairs(team_legs, lift_fn, top_n=10, min_games=500, min_both=50,
                    warn_below=2000):
    """Emit one lift-adjusted NRFI+F5 card per game that has both markets.

    team_legs are already normalized, floor-passing builder team legs (favorite
    side, market_prob >= floor). For each game with both a first_inning_runs and
    an f5_runs leg, correct the joint by the empirically-measured same-game lift
    (README §15.9 item 1). combined_odds is the product of the two shopped prices
    — a NON-PLACEABLE reference (a book same-game parlay is repriced/restricted);
    the honest quantity is the lift-adjusted joint_prob.

    Sample gate: drop a card whose lift is backed by < min_games games or < min_both
    joint co-occurrences (a degenerate side/line combo yields a noisy lift). Flag
    small_sample when lift_n < warn_below (~under one MLB season of shared history).
    Ranked by lift-adjusted joint_prob desc, top-N.
    """
    by_game = {}
    for leg in team_legs:
        by_game.setdefault(leg["game_id"], []).append(leg)
    cards = []
    for glegs in by_game.values():
        nrfi = next((l for l in glegs if l["market"] == "first_inning_runs"), None)
        f5 = next((l for l in glegs if l["market"] == "f5_runs"), None)
        if nrfi is None or f5 is None:
            continue
        lift, lift_n, both_n = lift_fn(nrfi["side"], f5["side"],
                                       nrfi["line_value"], f5["line_value"])
        if lift_n < min_games or both_n < min_both:
            continue
        joint = pair_joint_prob(nrfi["market_prob"], f5["market_prob"], lift)
        cards.append({
            "legs": [nrfi, f5],
            "combined_odds": nrfi["decimal_odds"] * f5["decimal_odds"],
            "joint_prob": joint,
            "lift": lift, "lift_n": lift_n, "both_n": both_n,
            "small_sample": lift_n < warn_below,
            "n_legs": 2,
        })
    cards.sort(key=lambda c: c["joint_prob"], reverse=True)
    return cards[:top_n]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_builder_core.py -k same_game -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add optimizer/builder_core.py tests/test_builder_core.py
git commit -m "feat(builder_core): pure same_game_pairs with lift adjustment + sample gate"
```

---

### Task 3: `save_builds` carries lift metadata via an additive `extra` wrapper

**Files:**
- Modify: `optimizer/builder.py` (`save_builds` gains `extra=None`; merge into the
  JSONB wrapper)
- Test: `tests/test_builder.py`

**Interfaces:**
- Consumes: existing `save_builds(engine, target_payout, results,
  parlay_class="across_game", sport="mlb")`; `construction_signature`.
- Produces: `save_builds(engine, target_payout, results, parlay_class="across_game",
  sport="mlb", extra=None)`. When `extra` is a dict, its keys are merged into the
  persisted `{"class", "sport", ...extra, "legs": [...]}` wrapper. Same-game rows
  also read per-`result` `lift`/`lift_n`/`both_n`/`small_sample` and write them at
  the wrapper level (they are a property of the card, not a leg).

**Design note:** the lift metadata is per-card, so it must come from each `result`
dict (produced by `same_game_pairs`), not a single `extra` for the whole batch.
`extra` is a static-per-batch dict (unused for lift here) — but simpler: read the
four keys directly off `r` when present. We take that approach: `save_builds` writes
any of `lift`/`lift_n`/`both_n`/`small_sample` found on a result into that row's
wrapper. `extra` is NOT added (YAGNI) — instead the wrapper picks up those four
optional keys per row. This keeps other classes byte-identical (their results lack
those keys).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_builder.py` (reuse the existing `_CapturingEngine` /
`_CapturingConn` fake-engine pattern already in that file — inspect it; it captures
executed SQL + params). Model the test on the existing `save_builds` tests there.

```python
def test_save_builds_writes_lift_metadata_for_same_game(monkeypatch):
    import json
    from optimizer import builder
    eng = _CapturingEngine(existing_legs=[])  # no dedup collisions
    result = {
        "joint_prob": 0.41, "combined_odds": 3.2,
        "lift": 1.30, "lift_n": 2100, "both_n": 1000, "small_sample": False,
        "legs": [
            {"kind": "team", "game_id": 1, "player_id": None, "stat_type": None,
             "market": "first_inning_runs", "side": "under", "american_odds": -120,
             "line_value": 0.5, "label": "nrfi", "market_prob": 0.56,
             "model_prob": None, "book": None},
            {"kind": "team", "game_id": 1, "player_id": None, "stat_type": None,
             "market": "f5_runs", "side": "under", "american_odds": -110,
             "line_value": 4.5, "label": "f5u", "market_prob": 0.57,
             "model_prob": None, "book": None},
        ],
    }
    builder.save_builds(eng, 0.0, [result], parlay_class="same_game_pair", sport="mlb")
    wrapper = json.loads(eng.last_insert_params["legs"])
    assert wrapper["class"] == "same_game_pair"
    assert wrapper["lift"] == 1.30 and wrapper["lift_n"] == 2100
    assert wrapper["both_n"] == 1000 and wrapper["small_sample"] is False
    assert len(wrapper["legs"]) == 2


def test_save_builds_omits_lift_keys_for_normal_classes(monkeypatch):
    import json
    from optimizer import builder
    eng = _CapturingEngine(existing_legs=[])
    result = {
        "joint_prob": 0.67, "combined_odds": 1.4,
        "legs": [
            {"kind": "player", "game_id": 1, "player_id": 9, "stat_type": "hits",
             "market": None, "side": "over", "american_odds": -200, "line_value": 0.5,
             "label": "x hits over 0.5", "market_prob": 0.66, "model_prob": None,
             "book": None},
            {"kind": "player", "game_id": 2, "player_id": 8, "stat_type": "hits",
             "market": None, "side": "over", "american_odds": -180, "line_value": 0.5,
             "label": "y hits over 0.5", "market_prob": 0.64, "model_prob": None,
             "book": None},
        ],
    }
    builder.save_builds(eng, 1.4, [result], parlay_class="across_game", sport="mlb")
    wrapper = json.loads(eng.last_insert_params["legs"])
    assert "lift" not in wrapper and "small_sample" not in wrapper
    assert set(wrapper.keys()) == {"class", "sport", "legs"}
```

> **Worker note:** inspect the real `_CapturingEngine` in `tests/test_builder.py`. If
> its constructor/attribute names differ from `existing_legs=` /
> `last_insert_params`, adapt these two tests to the actual fake-engine API (the
> existing `save_builds` tests in that file show the exact usage). Keep the
> assertions.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_builder.py -k "lift_metadata or omits_lift" -v`
Expected: FAIL — wrapper has no `lift` key.

- [ ] **Step 3: Implement the wrapper merge**

In `optimizer/builder.py:save_builds`, change the `INSERT`'s wrapper construction.
Replace the `json.dumps({"class": parlay_class, "sport": sport, "legs": legs_json},
allow_nan=False)` with a wrapper that conditionally includes the four lift keys read
off the result `r`:

```python
            wrapper = {"class": parlay_class, "sport": sport}
            # Same-game cards (README §15.9 item 1) carry correlation metadata at
            # the wrapper level — it's a property of the pair, not a leg. Absent on
            # every other class, so their persisted shape is byte-unchanged.
            for k in ("lift", "lift_n", "both_n", "small_sample"):
                if k in r:
                    wrapper[k] = r[k]
            wrapper["legs"] = legs_json
            conn.execute(
                text(
                    """
                    INSERT INTO parlay_recommendations
                        (kind, target_payout, legs, joint_prob, combined_odds)
                    VALUES ('builder', :tp, CAST(:legs AS JSONB), :jp, :co)
                    """
                ),
                {
                    "tp": target_payout,
                    "legs": json.dumps(wrapper, allow_nan=False),
                    "jp": r["joint_prob"],
                    "co": r["combined_odds"],
                },
            )
            rows += 1
```

(Keep the surrounding dedup logic — `sig`/`seen` — exactly as-is; only the
wrapper/`INSERT` block changes. `legs_json` is built as before. The `extra` param is
NOT added — the four keys are read off `r`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_builder.py -k "lift_metadata or omits_lift" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add optimizer/builder.py tests/test_builder.py
git commit -m "feat(builder): save_builds carries same-game lift metadata in the JSONB wrapper"
```

---

### Task 4: `--same-game` mode in `optimizer/builder.py`

**Files:**
- Modify: `optimizer/builder.py` (`main`: new `--same-game` flag + wiring + gate;
  add cached `lift_fn`); retire the dormant DB code in `optimizer/team_parlay.py`
- Test: `tests/test_builder.py`

**Interfaces:**
- Consumes: `load_team_legs`, `same_game_pairs` (Task 2), `save_builds` (Task 3),
  `modeling.correlation.nrfi_f5_lift` (Task 1).
- Produces: `python -m optimizer.builder --same-game [--save] [--sport mlb]
  [--top-n N]` — loads floor-passing team legs, builds cached lift-adjusted NRFI+F5
  cards, prints them, and (with `--save`) persists `class="same_game_pair"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_builder.py`. Test the mode's core wiring through a small helper we
extract for testability — a pure `build_same_game(legs, lift_fn, top_n)` that
`main()` calls. Add this helper to `builder.py` so the CLI stays thin.

```python
def test_build_same_game_helper_wires_pairs():
    from optimizer.builder import build_same_game
    legs = [
        {"game_id": 1, "market": "first_inning_runs", "side": "under",
         "market_prob": 0.56, "decimal_odds": 1.8, "american_odds": -120,
         "line_value": 0.5, "label": "nrfi", "book": None, "kind": "team"},
        {"game_id": 1, "market": "f5_runs", "side": "under", "market_prob": 0.57,
         "decimal_odds": 1.9, "american_odds": -110, "line_value": 4.5,
         "label": "f5u", "book": None, "kind": "team"},
    ]
    cards = build_same_game(legs, lambda sn, sf, nl, fl: (1.30, 2100, 1000), top_n=5)
    assert len(cards) == 1
    assert cards[0]["lift"] == 1.30 and cards[0]["n_legs"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_builder.py -k build_same_game -v`
Expected: FAIL — `cannot import name 'build_same_game'`.

- [ ] **Step 3: Implement the helper + CLI wiring**

In `optimizer/builder.py`, add the import at the top:

```python
from optimizer.builder_core import same_game_pairs
from modeling.correlation import nrfi_f5_lift
```

Add the thin helper (delegates to the pure fn — exists mainly so `main` stays thin
and is unit-testable without a DB):

```python
def build_same_game(team_legs, lift_fn, top_n=10):
    """Same-game NRFI+F5 cards from floor-passing team legs (README §15.9 item 1).
    Thin wrapper over builder_core.same_game_pairs with the default sample gate."""
    return same_game_pairs(team_legs, lift_fn, top_n=top_n)
```

Add the CLI flag in `main`'s argparse block (next to `--team-only`):

```python
    parser.add_argument("--same-game", action="store_true",
                        help="build the same-game NRFI+F5 combos class (README "
                             "§15.9 item 1): one lift-adjusted card per game with "
                             "both markets. Ignores --target-payout/--min-prob. "
                             "--save writes class=\"same_game_pair\".")
```

Add a guard after args are parsed (near the existing `--target-payout/--min-prob`
required check):

```python
    if args.same_game and args.team_only:
        parser.error("--same-game and --team-only are mutually exclusive")
```

And the same-game branch — place it as an early return path in `main` right after
`engine = db.get_engine()`, BEFORE the `if args.target_payout is None and
args.min_prob is None` check (so it doesn't require an axis pin):

```python
    engine = db.get_engine()

    if args.same_game:
        window_days = args.window_days if args.window_days is not None \
            else SLATE_WINDOW_DAYS.get(args.sport, 0)
        legs = load_team_legs(engine, args.floor, args.slate_date, args.sport, window_days)
        print(f"same-game team legs (favorite side, market prob >= {args.floor:.0%}): {len(legs)}")
        cache = {}
        def lift_fn(side_nrfi, side_f5, nrfi_line, f5_line):
            key = (side_nrfi, side_f5, nrfi_line, f5_line)
            if key not in cache:
                cache[key] = nrfi_f5_lift(engine, side_nrfi, side_f5, nrfi_line, f5_line)
            return cache[key]
        cards = build_same_game(legs, lift_fn, top_n=args.top_n)
        print(f"same-game cards (post-gate): {len(cards)}")
        for c in cards:
            warn = " [SMALL SAMPLE]" if c["small_sample"] else ""
            print(f"  ~{c['joint_prob']:.1%} joint  (lift x{c['lift']:.2f}, "
                  f"n={c['lift_n']} games{warn})  ref payout {c['combined_odds']:.2f}x "
                  f"— NOT a placeable same-game price")
            for leg in c["legs"]:
                print(f"      - {leg['label']} @ {leg['american_odds']:+d} "
                      f"(market {leg['market_prob']:.1%})")
        if args.save:
            saved = save_builds(engine, 0.0, cards, "same_game_pair", args.sport)
            print(f"parlay_recommendations (kind=builder, class=same_game_pair): "
                  f"inserted {saved} rows")
        return

    if args.target_payout is None and args.min_prob is None:
        parser.error("pin at least one axis: --target-payout and/or --min-prob")
```

> **Worker note:** the existing `main` currently computes `window_days` AFTER the
> axis-pin check. Leave that path intact for the non-same-game flow; the same-game
> branch computes its own `window_days` and returns before it. Do not duplicate the
> axis-pin `parser.error` — move the same-game branch above it as shown.

Then **retire the dormant DB code in `optimizer/team_parlay.py`**: delete
`load_team_legs`, `save_team_recommendations`, and `main` (they read the dropped
`game_edges` / write the retired `kind='team'` shape). KEEP the pure
`recommendation_ev` and `same_game_pairs` ONLY if something imports them — check with
`graphify query "who imports team_parlay"` / grep. If nothing imports `team_parlay`
after this, delete the whole file and remove `tests/test_team_parlay.py`'s dormant
DB-path tests (keep any pure tests by moving them, or delete if fully superseded by
`tests/test_correlation.py` + `tests/test_builder_core.py`).

> **Worker note (verify before deleting):** run `graphify query "imports of
> optimizer.team_parlay and modeling.correlation same_game_pairs"` and
> `grep -rn "team_parlay" --include=*.py .`. `modeling/correlation.py` stays (Task 1
> uses it). Only remove `team_parlay.py` symbols that are truly unreferenced. Report
> exactly what you deleted in your summary.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_builder.py tests/test_correlation.py tests/test_builder_core.py -v`
Expected: PASS. Also `.venv/bin/python -c "import optimizer.builder"` imports clean.

- [ ] **Step 5: Commit**

```bash
git add optimizer/builder.py optimizer/team_parlay.py tests/
git commit -m "feat(builder): --same-game mode; retire dormant team_parlay game_edges code"
```

---

### Task 5: API — `?tier=same_game` + `SavedBuilderParlayOut` lift fields

**Files:**
- Modify: `api/main.py` (`TIER_TO_CLASS`, `_CLASS_TO_TIER`, `_TIER_SORT_ORDER`,
  `saved_builder_parlays` to populate wrapper-level lift fields)
- Modify: `api/schemas.py` (`SavedBuilderParlayOut` additive optional fields)
- Test: `tests/test_parlay_builder_api.py`

**Interfaces:**
- Produces: `GET /parlay-builder/saved?tier=same_game&sport=mlb` returns
  `class='same_game_pair'` rows; `SavedBuilderParlayOut` gains
  `lift: float | None = None`, `lift_n: int | None = None`,
  `both_n: int | None = None`, `small_sample: bool = False`, read from the JSONB
  wrapper. `tier=player` byte-unchanged; new fields `None`/`False` for all other rows.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_parlay_builder_api.py` (mirror its existing fake-engine +
`main.engine` monkeypatch pattern — inspect the file; the leg-team-names tests there
show how rows are faked). The saved endpoint reads `r[5]` = the JSONB wrapper.

```python
def test_saved_same_game_tier_exposes_lift_fields(monkeypatch):
    # A same_game_pair row: wrapper carries lift metadata + 2 team legs.
    wrapper = {
        "class": "same_game_pair", "sport": "mlb",
        "lift": 1.30, "lift_n": 2100, "both_n": 1000, "small_sample": False,
        "legs": [
            {"kind": "team", "game_id": 1, "market": "first_inning_runs",
             "side": "under", "line": 0.5, "odds": -120, "market_prob": 0.56,
             "model_prob": None, "book": None, "label": "nrfi",
             "player_id": None, "stat_type": None},
            {"kind": "team", "game_id": 1, "market": "f5_runs",
             "side": "under", "line": 4.5, "odds": -110, "market_prob": 0.57,
             "model_prob": None, "book": None, "label": "f5u",
             "player_id": None, "stat_type": None},
        ],
    }
    # Fake row: (parlay_id, created_at, target_payout, joint_prob, combined_odds, legs)
    _install_fake_saved_rows(monkeypatch, [(1, "2026-08-07 10:00-04", 0.0, 0.41, 3.2, wrapper)])
    from fastapi.testclient import TestClient
    from api.main import app
    client = TestClient(app)
    resp = client.get("/parlay-builder/saved?tier=same_game&sport=mlb")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["lift"] == 1.30 and body[0]["lift_n"] == 2100
    assert body[0]["both_n"] == 1000 and body[0]["small_sample"] is False
    assert body[0]["n_legs"] == 2


def test_saved_player_tier_lift_fields_default_none(monkeypatch):
    wrapper = {
        "class": "across_game", "sport": "mlb",
        "legs": [
            {"kind": "player", "game_id": 1, "market": None, "side": "over",
             "line": 0.5, "odds": -200, "market_prob": 0.66, "model_prob": None,
             "book": None, "label": "x hits over 0.5", "player_id": 9,
             "stat_type": "hits"},
            {"kind": "player", "game_id": 2, "market": None, "side": "over",
             "line": 0.5, "odds": -180, "market_prob": 0.64, "model_prob": None,
             "book": None, "label": "y hits over 0.5", "player_id": 8,
             "stat_type": "hits"},
        ],
    }
    _install_fake_saved_rows(monkeypatch, [(2, "2026-08-07 10:00-04", 1.4, 0.42, 1.4, wrapper)])
    from fastapi.testclient import TestClient
    from api.main import app
    resp = TestClient(app).get("/parlay-builder/saved?tier=player&sport=mlb")
    assert resp.status_code == 200
    assert resp.json()[0]["lift"] is None
    assert resp.json()[0]["small_sample"] is False
```

> **Worker note:** `_install_fake_saved_rows` is a helper you write mirroring the
> existing fake-engine wiring in `tests/test_parlay_builder_api.py` — it must
> monkeypatch `api.main.engine` so `engine.begin()` yields a connection whose
> `.execute(...).fetchall()` returns the given rows, AND make
> `_load_builder_team_context` return empty maps (no games/players lookups). The
> file already fakes this for the leg-team-names search test; reuse that scaffold. If
> a shared helper already exists, use it instead of writing a new one.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_parlay_builder_api.py -k "same_game_tier or lift_fields_default" -v`
Expected: FAIL — unknown tier `same_game` (422) / no `lift` field on the response.

- [ ] **Step 3: Implement**

In `api/schemas.py`, add to `SavedBuilderParlayOut`:

```python
class SavedBuilderParlayOut(BuilderParlayOut):
    parlay_id: int
    created_at: str
    target_payout: float
    # Same-game combos (README §15.9 item 1) — correlation metadata read from the
    # legs JSONB wrapper. Additive/defaulted: None/False for every other class and
    # for Budgerr's player-tier consumption. lift is the empirical same-game lift,
    # lift_n the games it's measured over, both_n the joint-cell count, small_sample
    # true when the history is thin (< ~one season).
    lift: float | None = None
    lift_n: int | None = None
    both_n: int | None = None
    small_sample: bool = False
```

In `api/main.py`:

```python
TIER_TO_CLASS = {"player": "across_game", "team": "team_tier",
                 "game": "game_tier", "same_game": "same_game_pair"}
```
```python
_CLASS_TO_TIER = {"across_game": "player", "team_tier": "team",
                  "game_tier": "game", "same_game_pair": "same_game"}
```
```python
_TIER_SORT_ORDER = {"player": 0, "team": 1, "game": 2, "same_game": 3}
```

In `saved_builder_parlays`, the wrapper (`r[5]`) is already parsed by psycopg2. Read
its meta alongside the legs. Add a tiny helper next to `_as_legs_list`:

```python
def _wrapper_meta(raw):
    """Wrapper-level lift metadata for same-game rows; empty for bare-list / other
    classes (README §15.9 item 1)."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        return {k: raw.get(k) for k in ("lift", "lift_n", "both_n")} \
            | {"small_sample": bool(raw.get("small_sample", False))}
    return {"lift": None, "lift_n": None, "both_n": None, "small_sample": False}
```

Then in the `out.append(SavedBuilderParlayOut(...))` construction, thread the meta.
Change `parlays = [(r, _as_legs_list(r[5])) for r in rows]` to also carry the meta:

```python
    parlays = [(r, _as_legs_list(r[5]), _wrapper_meta(r[5])) for r in rows]
```

Update the two downstream comprehensions that unpack `parlays` (the `game_ids` /
`player_ids` sets and the final loop) to unpack the 3-tuple, and pass the meta:

```python
    game_ids = {
        leg["game_id"] for _, legs_raw, _ in parlays for leg in legs_raw
        if leg.get("game_id") is not None
    }
    player_ids = {
        leg["player_id"] for _, legs_raw, _ in parlays for leg in legs_raw
        if leg.get("player_id") is not None
    }
    games, players = _load_builder_team_context(engine, game_ids, player_ids)

    out = []
    for r, legs_raw, meta in parlays:
        out.append(
            SavedBuilderParlayOut(
                parlay_id=r[0], created_at=str(r[1]), target_payout=float(r[2]),
                joint_prob=float(r[3]), combined_odds=float(r[4]), n_legs=len(legs_raw),
                lift=meta["lift"], lift_n=meta["lift_n"], both_n=meta["both_n"],
                small_sample=meta["small_sample"],
                legs=[
                    BuilderLegOut(
                        game_id=leg["game_id"], kind=leg["kind"], label=leg["label"],
                        player_id=leg.get("player_id"), stat_type=leg.get("stat_type"),
                        market=leg.get("market"), side=leg["side"], line=leg["line"],
                        odds=leg["odds"], market_prob=leg["market_prob"],
                        model_prob=leg.get("model_prob"), book=leg.get("book"),
                        **_resolve_leg_teams(leg, games, players),
                    )
                    for leg in legs_raw
                ],
            )
        )
    return out
```

Also update the endpoint docstring's `tier` sentence to mention `same_game`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_parlay_builder_api.py -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add api/main.py api/schemas.py tests/test_parlay_builder_api.py
git commit -m "feat(api): same_game tier + lift fields on /parlay-builder/saved (additive)"
```

---

### Task 6: Dashboard — "Same-game combos" section (MLB)

**Files:**
- Modify: `web/app/lib/api.ts` (`SavedBuilderParlay` type gains optional lift fields;
  `getSavedBuilderParlays` already takes `tier`)
- Create: `web/app/builder/SameGameList.tsx` (client component)
- Modify: `web/app/builder/page.tsx` (fetch + render an MLB-only third section)
- Modify: `web/app/builder/builder.module.css` if a new caption style is needed
  (reuse `tierNote` where possible)

**Interfaces:**
- Consumes: `/parlay-builder/saved?tier=same_game&sport=mlb`.
- Produces: an MLB-only section under the team-market section showing each same-game
  card: legs, lift-adjusted joint prominent, "based on N games (lift ×1.30)", the
  non-placeable payout caption, and a small-sample warning when `small_sample`.

> **web/ required reading (do FIRST):** `PRODUCT.md`, `DESIGN.md`, and
> `web/node_modules/next/dist/docs/` (this Next is 16.x — `middleware.ts` is
> deprecated etc.). Match `web/app/builder/ConstructionList.tsx` +
> `page.tsx` conventions. Monochrome only — **no signal-green** (reserved for the
> ≥75% joint-prob rule). No +EV / edge / value language.

- [ ] **Step 1: Extend the TS type**

In `web/app/lib/api.ts`, find the `SavedBuilderParlay` type (returned by
`getSavedBuilderParlays`) and add the optional fields:

```typescript
export type SavedBuilderParlay = {
  // ...existing fields...
  lift?: number | null;
  lift_n?: number | null;
  both_n?: number | null;
  small_sample?: boolean;
};
```

> **Worker note:** graphify/grep for the exact current `SavedBuilderParlay` shape and
> add only the four fields — do not restate/alter existing fields.

- [ ] **Step 2: Create `SameGameList.tsx`**

`web/app/builder/SameGameList.tsx` — a `"use client"` component mirroring
`ConstructionList.tsx`'s card/leg rendering (reuse its `LegMatchup`/leg row markup by
importing the shared pieces if exported, else replicate the minimal leg row). Per
card render, in this order:
1. the two legs (NRFI + F5) with existing team-name/matchup rendering,
2. the **joint prominently** (e.g. `~41% both hit`) — the risk,
3. a muted meta line: `lift ×{lift} · based on {lift_n.toLocaleString()} games`,
4. the payout as reference: `ref payout {combined_odds}× — not a placeable
   same-game price (a book reprices or restricts correlated legs)`,
5. when `small_sample`: a prominent caption `⚠ small sample — correlation based on
   under a season of shared history`.
Honest empty state passed in via props (title/body), like `ConstructionList`.

Keep copy paper-framed; no green, no "value/edge".

- [ ] **Step 3: Wire the section into `page.tsx` (MLB only)**

In `web/app/builder/page.tsx`:
- Add `same_game` copy to `SPORT_CFG.mlb` only (a new optional `tier3` block):
  ```typescript
  tier3: {
    heading: "Same-game combos (NRFI + F5)",
    note: "NRFI and F5-under move together in low-scoring games, so the true chance both hit is higher than multiplying them suggests — we correct for that measured correlation and show how many games it's based on. The payout shown is a reference only: real sportsbooks reprice or restrict correlated same-game legs.",
    empty: {
      title: "No same-game combos tonight",
      body: "Both NRFI and F5 have to clear the safety floor in the same game, which is rare — an empty night here is normal.",
    },
  },
  ```
  Add `tier3?: {...}` to the other sports as `undefined` (only MLB has it), or gate
  rendering on `sport === "mlb"`.
- In the `Promise.all` fetch, add (MLB only):
  ```typescript
  const tier3Saved = sport === "mlb"
    ? await getSavedBuilderParlays(10, "same_game", "mlb")
    : [];
  ```
  (Fold into the existing `try` block; on error it stays `[]`.)
- Filter `tier3Saved` to `onLatestSlate` like the others → `tier3Latest`.
- Render a fourth `<section>` after the tier-2 section, only when `sport === "mlb"`
  and `cfg.tier3` exists:
  ```tsx
  {sport === "mlb" && cfg.tier3 && (
    <section className={styles.section} aria-label={cfg.tier3.heading}>
      <div className={styles.sectionHeader}>
        <h2 className={styles.sectionTitle}>{cfg.tier3.heading}</h2>
      </div>
      <p className={styles.tierNote}>{cfg.tier3.note}</p>
      <SameGameList
        constructions={tier3Latest}
        emptyTitle={cfg.tier3.empty.title}
        emptyBody={cfg.tier3.empty.body}
      />
    </section>
  )}
  ```
- Include `tier3Latest` in `noParlaysAtAll` only if you want an all-empty MLB page to
  still show the emptyAll copy — MLB's `emptyAll` is `null`, so leave
  `noParlaysAtAll` as-is (do NOT add tier3 to it).

- [ ] **Step 4: Typecheck + build**

Run (in `web/`): `npm run build` (or the repo's `tsc` check). Expected: clean, no
type errors. If `getSavedBuilderParlays`'s signature differs, adapt the call.

- [ ] **Step 5: Commit**

```bash
git add web/app/lib/api.ts web/app/builder/SameGameList.tsx web/app/builder/page.tsx web/app/builder/builder.module.css
git commit -m "feat(web): same-game combos dashboard section (MLB), honest lift + sample captions"
```

---

### Task 7: Chain step + README §15.9 item 1

**Files:**
- Modify: `scripts/daily_chain.sh` (one MLB `--same-game --save` step, non-fatal)
- Modify: `README.md` (§15.9 item 1 → BUILT status)

**Interfaces:** none (chain + docs). `scripts/daily_chain.sh` is NOT API-imported.

- [ ] **Step 1: Add the chain step**

In `scripts/daily_chain.sh`, in the MLB core build brace group, add the same-game
step immediately after `builder_team_2.0` (line ~193), non-fatal so an experimental
build can't abort the pre-game player card or downstream steps:

```bash
		_step builder_team_2.0 "$PY" -m optimizer.builder --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
		{ _step builder_same_game "$PY" -m optimizer.builder --same-game --top-n 5 --save || echo "=== builder_same_game: FAILED (non-fatal) ==="; } &&
		{ _nfl_weekly_build || echo "=== nfl weekly build: FAILED (non-fatal, MLB chain continues) ==="; } &&
```

Verify: `bash -n scripts/daily_chain.sh` is clean.

- [ ] **Step 2: Update README §15.9 item 1**

Rewrite item 1 in `README.md` §15.9 to a BUILT entry, following the house style of
items 3/4 (what/how/mechanics/verified/tests). Include: the empirical-lift approach
measured at real lines (n≈6,588; under/under 1.30), the pure `same_game_pairs` in
`builder_core.py` re-sourcing legs via `builder.load_team_legs` (NOT the dropped
`game_edges`), the sample gate (n≥500 & both_n≥50; small-sample warn < 2000), the
non-placeable-product-payout honesty framing, the additive `?tier=same_game` + lift
fields (Budgerr-safe), the dashboard section, the non-fatal nightly MLB chain step,
settlement/record/staking unchanged (record doubles as the lift's empirical check),
and that it is the deliberate labelled exception to the across-game-only guardrail.
Link the spec + this plan.

- [ ] **Step 3: Commit**

```bash
git add scripts/daily_chain.sh README.md
git commit -m "feat(chain)+docs(README §15.9 item 1): nightly same-game build; item 1 BUILT"
```

---

## Self-Review

**Spec coverage:** §2 grounding → Task 1 (lift fn). §4.1 → Task 1. §4.2 → Task 2.
§4.3 → Task 4. §4.4 → Task 3. §4.5 → Task 5. §4.6 → Task 6. §4.7 → Task 7. §5
(settlement/record/staking no-change) → verified in rollout (architect live check),
not a code task. §3 judgements → Tasks 1 (lines), 2 (gate), 5/6 (payout honesty +
sample surface). §6 guardrails → enforced by reusing `load_team_legs` (floor,
favorite, devig) + no-green copy in Task 6. §7 testing → each task's tests.

**Placeholder scan:** no TBD/TODO; every code step shows code; worker-notes point at
concrete existing patterns to mirror, not vague instructions.

**Type consistency:** `nrfi_f5_lift(engine, side_nrfi, side_f5, nrfi_line, f5_line)
-> (lift, n_games, both_n)` consistent across Tasks 1/2/4. `same_game_pairs(...)`
card keys (`lift/lift_n/both_n/small_sample/joint_prob/combined_odds/legs/n_legs`)
consistent across Tasks 2/3/4/5/6. Wrapper keys (`class/sport/lift/lift_n/both_n/
small_sample/legs`) consistent across Tasks 3/5. Tier maps (`same_game` ↔
`same_game_pair`) consistent Task 5.

## Post-implementation (architect reserved lanes — NOT worker steps)

1. Full suite: `.venv/bin/python -m pytest` — all green.
2. `launchctl kickstart -k gui/$(id -u)/com.playstat.api` (builder/builder_core/main
   are API-imported).
3. Live verify: `/parlay-builder/saved?tier=same_game&sport=mlb` → 200 (empty OK);
   `tier=player` byte-unchanged (diff against a pre-change capture); force a
   same-game build on a read-only/reverted eligible game to confirm lift + captions;
   browser section renders (no-auth preview, `web-noauth` launch config).
4. Confirm `settle_builder_parlays` scores a 2-leg same-game card (two team legs, one
   game_id) — read-only check against a settled example or a fake-engine unit check.
5. Push to main.
