# Low-Risk Parlay Builder — Stage 1 (Engine + API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a market-centric, low-risk parlay builder that constructs across-game parlays from MLB player props + team markets (NRFI/F5), ranked purely on devigged market probability, with a two-axis payout/probability interaction and honest risk surfacing.

**Architecture:** New `optimizer/builder.py` loads candidate legs by devigging the raw two-sided odds in `prop_lines`/`game_lines` and keeping the *favorite* side above a probability floor, normalizes player and team legs into one schema, searches across-game combinations of 2–4 legs, filters/ranks on a two-axis (target payout / minimum joint probability) interface, and persists to `parlay_recommendations` with `kind='builder'`. A new mixed-leg settlement path scores them. A read-only API endpoint serves live builds.

**Tech Stack:** Python 3.11, pandas, SQLAlchemy (`text()` + `engine.begin()`), FastAPI, pytest, PostgreSQL.

## Global Constraints

- **Rank ONLY on devigged market probability.** Never rank or filter on `model_prob`. `model_prob` is display context only, always labelled "not used for ranking".
- **No "+EV" / "edge" / "value" / "beat the market" claims** in code, API payloads, recommendation JSONB, comments, or output. The `ev` field from `team_parlay.py`'s wrapper is **dropped**.
- **`edges` is model-centric; the builder is market-centric.** `edges.side`/`edges.implied_prob` hold the side the *model* prefers (possibly an underdog) — see `modeling/edges.py` L92–95. **Never rank on `edges.implied_prob`.** Join `edges`/`game_edges` only to attach `model_prob`.
- **Across-game only.** No two legs in a parlay may share a `game_id`. This is what makes the independent product valid.
- **Favorite-side legs only**, `market_prob >= floor` (default `0.55`).
- **Leg count 2–4**, preferring fewest.
- Per-leg probability floor and candidate cap must bound `C(N, max_legs) <= ~5_000_000`.
- Python 3.11. Repo venv: `/Users/aayushpokhrel/dev/playstat/.venv`.
- Tests must be **DB-free** and pass under a stripped environment (`env -i`), like the existing suite (root `conftest.py` sets dummy `DATABASE_URL`).
- **Never** run `predict_upcoming`, `edges`, or anything that writes live tables. DB **reads are fine**; all evaluation in-memory.
- **Never** touch `~/Library/LaunchAgents/`, the live `:8000` API, or run `git push`.

---

## File Structure

| File | Responsibility |
|---|---|
| `optimizer/builder_core.py` (create) | Pure, DB-free math: devig-to-favorite, leg normalization, floor filter, combination search, two-axis filter/rank, candidate cap. |
| `optimizer/builder.py` (create) | DB layer + CLI: load legs from `prop_lines`/`game_lines`, attach `model_prob`, persist to `parlay_recommendations`, `main()`. |
| `modeling/settle.py` (modify) | Add `settle_builder_parlays()` for **mixed** player+team legs (`kind='builder'`). |
| `tests/test_builder_core.py` (create) | Pure-math tests for `builder_core`. |
| `tests/test_settle_builder.py` (create) | Pure-math tests for mixed-leg settlement dispatch. |
| `api/schemas.py` (modify) | `BuilderLegOut`, `BuilderParlayOut`. |
| `api/main.py` (modify) | `GET /parlay-builder`. |

---

### Task 1: Pure core — favorite-side selection, leg normalization, floor

**Files:**
- Create: `optimizer/builder_core.py`
- Test: `tests/test_builder_core.py`

**Interfaces:**
- Consumes: `modeling.edges.devig`, `optimizer.parlay.american_to_decimal`.
- Produces: `favorite_side(over_odds, under_odds) -> tuple[str, float]`; `normalize_player_leg(row) -> dict`; `normalize_team_leg(row) -> dict`; `passes_floor(leg, floor) -> bool`; module constant `DEFAULT_FLOOR = 0.55`. Leg dict keys: `game_id, kind, label, player_id, stat_type, market, side, line_value, american_odds, decimal_odds, market_prob, model_prob`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_builder_core.py
import pytest
from optimizer.builder_core import (
    favorite_side, normalize_player_leg, normalize_team_leg,
    passes_floor, DEFAULT_FLOOR,
)


def test_favorite_side_picks_higher_devigged_side():
    # -200 over / +170 under: over is the favorite
    side, prob = favorite_side(-200, 170)
    assert side == "over"
    assert 0.5 < prob < 1.0


def test_favorite_side_picks_under_when_under_is_favorite():
    side, prob = favorite_side(170, -200)
    assert side == "under"
    assert 0.5 < prob < 1.0


def test_favorite_side_devigs_so_probability_is_below_raw_implied():
    # raw implied for -200 is 0.6667; devigged must be lower (vig removed)
    _, prob = favorite_side(-200, 170)
    assert prob < 200 / 300


def test_favorite_side_even_market_is_half():
    side, prob = favorite_side(100, -100)
    assert prob == pytest.approx(0.5, abs=0.02)


def test_passes_floor_rejects_below_floor():
    assert not passes_floor({"market_prob": 0.54}, 0.55)
    assert passes_floor({"market_prob": 0.55}, 0.55)
    assert passes_floor({"market_prob": 0.80}, 0.55)


def test_default_floor_is_055():
    assert DEFAULT_FLOOR == 0.55


def test_normalize_player_leg_shape():
    leg = normalize_player_leg({
        "player_id": 7, "game_id": 100, "stat_type": "total_bases",
        "line_value": 1.5, "over_odds": -200, "under_odds": 170,
        "player_name": "Judge", "model_prob": 0.61,
    })
    assert leg["kind"] == "player"
    assert leg["game_id"] == 100
    assert leg["player_id"] == 7
    assert leg["stat_type"] == "total_bases"
    assert leg["market"] is None
    assert leg["side"] == "over"
    assert leg["decimal_odds"] == pytest.approx(1.5)
    assert 0.5 < leg["market_prob"] < 1.0
    assert leg["model_prob"] == 0.61
    assert "Judge" in leg["label"]


def test_normalize_team_leg_shape():
    leg = normalize_team_leg({
        "game_id": 200, "market": "first_inning_runs",
        "line_value": 0.5, "over_odds": 150, "under_odds": -180,
        "model_prob": None,
    })
    assert leg["kind"] == "team"
    assert leg["player_id"] is None
    assert leg["stat_type"] is None
    assert leg["market"] == "first_inning_runs"
    assert leg["side"] == "under"
    assert leg["model_prob"] is None


def test_normalize_player_leg_keeps_model_prob_optional():
    leg = normalize_player_leg({
        "player_id": 1, "game_id": 2, "stat_type": "hits",
        "line_value": 0.5, "over_odds": -300, "under_odds": 240,
        "player_name": "X", "model_prob": None,
    })
    assert leg["model_prob"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_builder_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimizer.builder_core'`

- [ ] **Step 3: Write minimal implementation**

```python
# optimizer/builder_core.py
"""Pure, DB-free core for the low-risk parlay builder.

MARKET-centric by design: every probability here comes from de-vigging the
book's two-sided price, never from a model. README §15 explains why — the
models lack per-game resolution and overstate heavy-favorite safety, so the
book's de-vigged price is the best-calibrated probability available.
"""

from modeling.edges import devig
from optimizer.parlay import american_to_decimal

# No single leg may be worse than this to hit (de-vigged market probability).
DEFAULT_FLOOR = 0.55


def favorite_side(over_odds, under_odds):
    """(side, de-vigged probability) for whichever side the market makes the favorite."""
    p_over, p_under = devig(over_odds, under_odds)
    if p_over >= p_under:
        return "over", p_over
    return "under", p_under


def passes_floor(leg, floor=DEFAULT_FLOOR):
    return leg["market_prob"] >= floor


def _base_leg(game_id, side, market_prob, line_value, american_odds, model_prob, label):
    return {
        "game_id": int(game_id),
        "label": label,
        "side": side,
        "line_value": float(line_value),
        "american_odds": int(american_odds),
        "decimal_odds": american_to_decimal(int(american_odds)),
        "market_prob": float(market_prob),
        "model_prob": None if model_prob is None else float(model_prob),
    }


def normalize_player_leg(row):
    side, prob = favorite_side(row["over_odds"], row["under_odds"])
    odds = row["over_odds"] if side == "over" else row["under_odds"]
    label = f"{row.get('player_name', 'player')} {row['stat_type']} {side} {row['line_value']}"
    leg = _base_leg(row["game_id"], side, prob, row["line_value"], odds,
                    row.get("model_prob"), label)
    leg.update({"kind": "player", "player_id": int(row["player_id"]),
                "stat_type": row["stat_type"], "market": None})
    return leg


def normalize_team_leg(row):
    side, prob = favorite_side(row["over_odds"], row["under_odds"])
    odds = row["over_odds"] if side == "over" else row["under_odds"]
    label = f"{row['market']} {side} {row['line_value']}"
    leg = _base_leg(row["game_id"], side, prob, row["line_value"], odds,
                    row.get("model_prob"), label)
    leg.update({"kind": "team", "player_id": None, "stat_type": None,
                "market": row["market"]})
    return leg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_builder_core.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add optimizer/builder_core.py tests/test_builder_core.py
git commit -m "feat(builder): market-centric leg normalization + favorite-side devig"
```

---

### Task 2: Combination search, two-axis filter/rank, candidate cap

**Files:**
- Modify: `optimizer/builder_core.py`
- Test: `tests/test_builder_core.py`

**Interfaces:**
- Consumes: leg dicts from Task 1.
- Produces: `cap_candidates(legs, max_legs, max_combos=5_000_000) -> list`; `build(legs, target_payout=None, tolerance=0.15, min_prob=None, min_legs=2, max_legs=4, top_n=10) -> list[dict]` where each result is `{"legs": [...], "combined_odds": float, "joint_prob": float, "n_legs": int}`. Constants `DEFAULT_TOLERANCE = 0.15`, `DEFAULT_MIN_LEGS = 2`, `DEFAULT_MAX_LEGS = 4`, `MAX_COMBOS = 5_000_000`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_builder_core.py
from optimizer.builder_core import build, cap_candidates, MAX_COMBOS


def _leg(game_id, prob, dec_odds, kind="player"):
    return {
        "game_id": game_id, "kind": kind, "label": f"g{game_id}",
        "player_id": 1 if kind == "player" else None,
        "stat_type": "hits" if kind == "player" else None,
        "market": None if kind == "player" else "f5_runs",
        "side": "over", "line_value": 0.5, "american_odds": -150,
        "decimal_odds": dec_odds, "market_prob": prob, "model_prob": None,
    }


def test_build_excludes_same_game_combos():
    legs = [_leg(1, 0.7, 1.4), _leg(1, 0.7, 1.4)]
    assert build(legs, target_payout=1.96, tolerance=0.5) == []


def test_build_joint_prob_is_product_and_odds_is_product():
    legs = [_leg(1, 0.8, 1.25), _leg(2, 0.8, 1.25)]
    out = build(legs, target_payout=1.5625, tolerance=0.01)
    assert len(out) == 1
    assert out[0]["joint_prob"] == pytest.approx(0.64)
    assert out[0]["combined_odds"] == pytest.approx(1.5625)
    assert out[0]["n_legs"] == 2


def test_build_pin_payout_ranks_by_joint_prob_desc():
    legs = [_leg(1, 0.9, 1.25), _leg(2, 0.9, 1.25), _leg(3, 0.5, 1.25), _leg(4, 0.5, 1.25)]
    out = build(legs, target_payout=1.5625, tolerance=0.01)
    probs = [r["joint_prob"] for r in out]
    assert probs == sorted(probs, reverse=True)
    assert probs[0] == pytest.approx(0.81)


def test_build_pin_min_prob_ranks_by_payout_desc():
    legs = [_leg(1, 0.9, 1.2), _leg(2, 0.9, 1.2), _leg(3, 0.8, 2.0), _leg(4, 0.8, 2.0)]
    out = build(legs, min_prob=0.6)
    assert all(r["joint_prob"] >= 0.6 for r in out)
    odds = [r["combined_odds"] for r in odds_src] if False else [r["combined_odds"] for r in out]
    assert odds == sorted(odds, reverse=True)


def test_build_respects_leg_bounds():
    legs = [_leg(i, 0.9, 1.1) for i in range(1, 7)]
    out = build(legs, min_prob=0.0, min_legs=2, max_legs=3)
    assert out
    assert all(2 <= r["n_legs"] <= 3 for r in out)


def test_build_both_axes_pinned_filters_both():
    legs = [_leg(1, 0.9, 1.25), _leg(2, 0.9, 1.25), _leg(3, 0.5, 1.25), _leg(4, 0.5, 1.25)]
    out = build(legs, target_payout=1.5625, tolerance=0.01, min_prob=0.7)
    assert len(out) == 1
    assert out[0]["joint_prob"] == pytest.approx(0.81)


def test_build_respects_top_n():
    legs = [_leg(i, 0.9, 1.25) for i in range(1, 8)]
    out = build(legs, target_payout=1.5625, tolerance=0.01, top_n=3)
    assert len(out) == 3


def test_build_no_legs_returns_empty():
    assert build([], target_payout=2.0) == []


def test_cap_candidates_bounds_combination_count():
    legs = [_leg(i, 0.5 + (i % 50) / 200, 1.3) for i in range(1, 501)]
    capped = cap_candidates(legs, max_legs=4, max_combos=100_000)
    from math import comb
    assert comb(len(capped), 4) <= 100_000
    assert len(capped) < len(legs)


def test_cap_candidates_keeps_highest_market_prob():
    legs = [_leg(1, 0.60, 1.3), _leg(2, 0.90, 1.3), _leg(3, 0.70, 1.3)]
    capped = cap_candidates(legs, max_legs=2, max_combos=1)
    assert capped[0]["market_prob"] == 0.90


def test_cap_candidates_noop_when_already_small():
    legs = [_leg(1, 0.6, 1.3), _leg(2, 0.6, 1.3)]
    assert len(cap_candidates(legs, max_legs=2, max_combos=MAX_COMBOS)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_builder_core.py -v`
Expected: FAIL — `ImportError: cannot import name 'build'`

- [ ] **Step 3: Write minimal implementation**

Append to `optimizer/builder_core.py`:

```python
import itertools
from math import comb

DEFAULT_TOLERANCE = 0.15
DEFAULT_MIN_LEGS = 2
DEFAULT_MAX_LEGS = 4
# Bounds the brute-force search. The uncapped player optimizer was OOM-killed
# (SIGKILL) on 2026-07-18 at ~198M combinations — see README §11/§15.
MAX_COMBOS = 5_000_000


def cap_candidates(legs, max_legs=DEFAULT_MAX_LEGS, max_combos=MAX_COMBOS):
    """Keep the highest-market-probability legs such that C(n, max_legs) <= max_combos."""
    legs = sorted(legs, key=lambda leg: leg["market_prob"], reverse=True)
    if len(legs) <= max_legs:
        return legs
    n = len(legs)
    while n > max_legs and comb(n, max_legs) > max_combos:
        n -= 1
    return legs[:n]


def build(legs, target_payout=None, tolerance=DEFAULT_TOLERANCE, min_prob=None,
          min_legs=DEFAULT_MIN_LEGS, max_legs=DEFAULT_MAX_LEGS, top_n=10):
    """Across-game parlay constructions, two-axis filtered and ranked.

    Pin target_payout -> rank by joint probability (safest route to that payout).
    Pin min_prob      -> rank by payout (biggest payout at that safety level).
    Legs from the same game are never combined: the joint probability is a plain
    product, which is only valid for independent (different-game) legs.
    """
    if not legs:
        return []

    results = []
    for size in range(min_legs, max_legs + 1):
        for combo in itertools.combinations(legs, size):
            game_ids = [leg["game_id"] for leg in combo]
            if len(set(game_ids)) != len(game_ids):
                continue

            combined_odds = 1.0
            joint_prob = 1.0
            for leg in combo:
                combined_odds *= leg["decimal_odds"]
                joint_prob *= leg["market_prob"]

            if target_payout is not None and \
                    abs(combined_odds - target_payout) / target_payout > tolerance:
                continue
            if min_prob is not None and joint_prob < min_prob:
                continue

            results.append({
                "legs": list(combo),
                "combined_odds": combined_odds,
                "joint_prob": joint_prob,
                "n_legs": size,
            })

    # Pinning the probability floor means the user asked "how much can I win at
    # this safety level" -> rank by payout. Otherwise rank by safety.
    if target_payout is None and min_prob is not None:
        results.sort(key=lambda r: r["combined_odds"], reverse=True)
    else:
        results.sort(key=lambda r: r["joint_prob"], reverse=True)
    return results[:top_n]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_builder_core.py -v`
Expected: PASS (all tests)

Note: `test_build_pin_min_prob_ranks_by_payout_desc` contains a deliberate dead sub-expression (`if False else`); simplify that line to `odds = [r["combined_odds"] for r in out]` before committing.

- [ ] **Step 5: Commit**

```bash
git add optimizer/builder_core.py tests/test_builder_core.py
git commit -m "feat(builder): across-game search, two-axis filter/rank, combinatorial cap"
```

---

### Task 3: DB leg loading, persistence, CLI

**Files:**
- Create: `optimizer/builder.py`

**Interfaces:**
- Consumes: `optimizer.builder_core.{normalize_player_leg, normalize_team_leg, passes_floor, cap_candidates, build, DEFAULT_FLOOR, DEFAULT_MAX_LEGS}`.
- Produces: `load_player_legs(engine, floor)`, `load_team_legs(engine, floor)`, `load_legs(engine, floor)`, `save_builds(engine, target_payout, results)`, `main()`.

**Live-DB rule:** reads only. Do **not** execute `save_builds` against the live DB — the architect runs live writes after review.

- [ ] **Step 1: Write the implementation**

```python
# optimizer/builder.py
"""Low-risk parlay builder (README §15).

Constructs across-game parlays from MLB player props + team markets, ranked by
DE-VIGGED MARKET probability. This is an honest constructor and paper-trading
sandbox: it makes no claim of edge or positive expected value.

Market-centric on purpose: it de-vigs the raw two-sided odds itself and takes
the FAVORITE side. It deliberately does not read `edges.side`/`edges.implied_prob`,
which hold the side the MODEL prefers (possibly an underdog) — see README §15.4.
"""

import argparse
import json

import pandas as pd
from sqlalchemy import text

from ingestion import db
from optimizer.builder_core import (
    DEFAULT_FLOOR, DEFAULT_MAX_LEGS, DEFAULT_MIN_LEGS, DEFAULT_TOLERANCE,
    build, cap_candidates, normalize_player_leg, normalize_team_leg, passes_floor,
)

TEAM_MARKETS = ("first_inning_runs", "f5_runs")


def load_player_legs(engine, floor=DEFAULT_FLOOR):
    """Latest two-sided player prop lines on unfinished games, + model_prob context."""
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT pl.player_id, pl.game_id, pl.stat_type, pl.line_value,
                       pl.over_odds, pl.under_odds, p.name AS player_name,
                       e.model_prob
                FROM (
                    SELECT DISTINCT ON (player_id, game_id, stat_type)
                        player_id, game_id, stat_type, line_value, over_odds, under_odds
                    FROM prop_lines
                    ORDER BY player_id, game_id, stat_type, pulled_at DESC
                ) pl
                JOIN games g ON g.game_id = pl.game_id AND g.status != 'FT'
                JOIN players p ON p.player_id = pl.player_id
                LEFT JOIN edges e ON e.player_id = pl.player_id
                    AND e.game_id = pl.game_id AND e.stat_type = pl.stat_type
                """
            ),
            conn,
        )
    return _normalize(df, normalize_player_leg, floor)


def load_team_legs(engine, floor=DEFAULT_FLOOR):
    """Latest two-sided team-market lines on unfinished games, + model_prob context."""
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT gl.game_id, gl.market, gl.line_value, gl.over_odds, gl.under_odds,
                       ge.model_prob
                FROM (
                    SELECT DISTINCT ON (game_id, market)
                        game_id, market, line_value, over_odds, under_odds
                    FROM game_lines
                    WHERE market = ANY(:markets)
                    ORDER BY game_id, market, pulled_at DESC
                ) gl
                JOIN games g ON g.game_id = gl.game_id AND g.status != 'FT'
                LEFT JOIN game_edges ge ON ge.game_id = gl.game_id AND ge.market = gl.market
                """
            ),
            conn, params={"markets": list(TEAM_MARKETS)},
        )
    return _normalize(df, normalize_team_leg, floor)


def _normalize(df, normalizer, floor):
    if df.empty:
        return []
    # A book quoting only one side can't be de-vigged (~8% of live MLB lines).
    df = df.dropna(subset=["over_odds", "under_odds", "line_value"])
    if df.empty:
        return []
    df = df.where(pd.notna(df), None)
    legs = [normalizer(row) for row in df.to_dict("records")]
    return [leg for leg in legs if passes_floor(leg, floor)]


def load_legs(engine, floor=DEFAULT_FLOOR):
    return load_player_legs(engine, floor) + load_team_legs(engine, floor)


def save_builds(engine, target_payout, results):
    """Persist constructions. No EV/edge field is written — this builder makes no such claim."""
    rows = 0
    with engine.begin() as conn:
        for r in results:
            legs_json = [
                {
                    "kind": leg["kind"], "game_id": leg["game_id"],
                    "player_id": leg["player_id"], "stat_type": leg["stat_type"],
                    "market": leg["market"], "side": leg["side"],
                    "odds": leg["american_odds"], "line": leg["line_value"],
                    "label": leg["label"], "market_prob": leg["market_prob"],
                    "model_prob": leg["model_prob"],
                }
                for leg in r["legs"]
            ]
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
                    "legs": json.dumps({"class": "across_game", "legs": legs_json}),
                    "jp": r["joint_prob"],
                    "co": r["combined_odds"],
                },
            )
            rows += 1
    return rows


def main():
    parser = argparse.ArgumentParser(description="Low-risk parlay builder (no edge/EV claim).")
    parser.add_argument("--target-payout", type=float, default=None)
    parser.add_argument("--min-prob", type=float, default=None)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--floor", type=float, default=DEFAULT_FLOOR)
    parser.add_argument("--min-legs", type=int, default=DEFAULT_MIN_LEGS)
    parser.add_argument("--max-legs", type=int, default=DEFAULT_MAX_LEGS)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--save", action="store_true", help="persist to parlay_recommendations")
    args = parser.parse_args()

    if args.target_payout is None and args.min_prob is None:
        parser.error("pin at least one axis: --target-payout and/or --min-prob")

    engine = db.get_engine()
    legs = load_legs(engine, args.floor)
    print(f"candidate legs (favorite side, market prob >= {args.floor:.0%}): {len(legs)}")
    if not legs:
        print("no candidate legs — nothing to build.")
        return

    legs = cap_candidates(legs, args.max_legs)
    print(f"searching {len(legs)} legs, {args.min_legs}-{args.max_legs} per parlay")

    results = build(legs, target_payout=args.target_payout, tolerance=args.tolerance,
                    min_prob=args.min_prob, min_legs=args.min_legs,
                    max_legs=args.max_legs, top_n=args.top_n)
    print(f"constructions found: {len(results)}")
    for r in results:
        print(f"  {r['combined_odds']:.2f}x  ~{r['joint_prob']:.1%} to hit  "
              f"({r['n_legs']} legs)")
        for leg in r["legs"]:
            print(f"      - {leg['label']} @ {leg['american_odds']:+d} "
                  f"(market {leg['market_prob']:.1%})")

    if args.save:
        saved = save_builds(engine, args.target_payout or 0.0, results)
        print(f"parlay_recommendations (kind=builder): inserted {saved} rows")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports and the CLI validates arguments**

```bash
.venv/bin/python -c "import optimizer.builder; print('import ok')"
.venv/bin/python -m optimizer.builder 2>&1 | tail -2
```
Expected: `import ok`, then an error `pin at least one axis: --target-payout and/or --min-prob`.

- [ ] **Step 3: Verify leg loading against the live DB (READ ONLY — do not pass --save)**

```bash
.venv/bin/python -m optimizer.builder --target-payout 2.0 --top-n 3
```
Expected: prints a candidate-leg count and up to 3 constructions with payout, "% to hit", and per-leg market probabilities. Zero legs is an acceptable result if no unfinished games have two-sided lines right now — report exactly what you saw.

- [ ] **Step 4: Commit**

```bash
git add optimizer/builder.py
git commit -m "feat(builder): DB leg loading, persistence, CLI"
```

---

### Task 4: Mixed player+team parlay settlement

**Why this task exists:** `settle_parlays` (`kind='player'`) requires `player_id` on every leg; `settle_team_parlays` (`kind='team'`) requires `market` on every leg. The builder emits **mixed** parlays, which neither can score. This adds a third path that dispatches **per leg**.

**Files:**
- Modify: `modeling/settle.py`
- Test: `tests/test_settle_builder.py`

**Interfaces:**
- Consumes: existing `settle_leg`, `parlay_result`, `american_to_decimal`, `_as_legs_list`, `_rec_snapshot`, `MARKET_TO_STAT`.
- Produces: `builder_leg_key(leg) -> tuple`; `settle_builder_parlays(engine) -> int`. `settle()` also calls it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settle_builder.py
import pytest
from modeling.settle import builder_leg_key, settle_leg, parlay_result


def test_builder_leg_key_player():
    leg = {"kind": "player", "game_id": 10, "player_id": 3, "stat_type": "hits", "market": None}
    assert builder_leg_key(leg) == ("player", 3, 10, "hits")


def test_builder_leg_key_team():
    leg = {"kind": "team", "game_id": 11, "player_id": None, "stat_type": None,
           "market": "f5_runs"}
    assert builder_leg_key(leg) == ("team", 11, "f5_runs")


def test_builder_leg_key_rejects_unknown_kind():
    with pytest.raises(ValueError):
        builder_leg_key({"kind": "spaceship", "game_id": 1})


# NOTE: settle_leg() returns "hit"/"miss"/"push" (per-leg).
# "win"/"loss" is what parlay_result() returns for the parlay as a whole.
# An earlier draft of this plan conflated the two — corrected 2026-07-21.
def test_mixed_parlay_all_hit_wins():
    results = [settle_leg("over", 2.0, 1.5), settle_leg("under", 0.0, 0.5)]
    assert results == ["hit", "hit"]
    result, odds, pnl = parlay_result(results, [1.5, 1.4])
    assert result == "win"
    assert odds == pytest.approx(2.1)
    assert pnl == pytest.approx(1.1)


def test_mixed_parlay_one_miss_loses():
    results = [settle_leg("over", 2.0, 1.5), settle_leg("under", 3.0, 0.5)]
    assert results == ["hit", "miss"]
    result, _, pnl = parlay_result(results, [1.5, 1.4])
    assert result == "loss"
    assert pnl == pytest.approx(-1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_settle_builder.py -v`
Expected: FAIL — `ImportError: cannot import name 'builder_leg_key'`

- [ ] **Step 3: Write minimal implementation**

Add to `modeling/settle.py` (after `settle_team_parlays`):

```python
def builder_leg_key(leg):
    """Lookup key for a builder leg, dispatched on its kind.

    Builder parlays mix player-prop and team-market legs in one bet, so each leg
    must resolve against a different source table — unlike settle_parlays /
    settle_team_parlays, which each assume a homogeneous leg list.
    """
    kind = leg.get("kind")
    if kind == "player":
        return ("player", int(leg["player_id"]), int(leg["game_id"]), leg["stat_type"])
    if kind == "team":
        return ("team", int(leg["game_id"]), leg["market"])
    raise ValueError(f"unknown builder leg kind: {kind!r}")


def settle_builder_parlays(engine):
    with engine.begin() as conn:
        candidates = conn.execute(
            text(
                """
                SELECT pr.parlay_id, pr.created_at, pr.legs
                FROM parlay_recommendations pr
                WHERE pr.kind = 'builder' AND NOT EXISTS (
                    SELECT 1 FROM recommendation_outcomes ro
                    WHERE ro.bet_type = 'parlay' AND ro.parlay_id = pr.parlay_id)
                """
            )
        ).fetchall()
        if not candidates:
            print("settle: no new builder parlays to evaluate.")
            return 0

        parsed = [(pid, ca, _as_legs_list(raw)) for pid, ca, raw in candidates]
        parsed = [(pid, ca, blob["legs"] if isinstance(blob, dict) else blob)
                  for pid, ca, blob in parsed]
        game_ids = sorted({int(l["game_id"]) for _, _, legs in parsed for l in legs})

        games = pd.read_sql(text("SELECT game_id, status FROM games WHERE game_id = ANY(:g)"),
                            conn, params={"g": game_ids})
        pstats = pd.read_sql(
            text("""SELECT player_id, game_id, stat_type, value
                    FROM player_game_stats WHERE game_id = ANY(:g)"""),
            conn, params={"g": game_ids})
        tstats = pd.read_sql(
            text("""SELECT game_id, stat_type, SUM(value) AS total
                    FROM team_game_stats
                    WHERE game_id = ANY(:g) AND stat_type IN ('runs_inning_1','runs_f5')
                    GROUP BY game_id, stat_type"""),
            conn, params={"g": game_ids})
        plines = pd.read_sql(
            text("""SELECT player_id, game_id, stat_type, line_value, pulled_at
                    FROM prop_lines WHERE game_id = ANY(:g) ORDER BY pulled_at"""),
            conn, params={"g": game_ids})
        glines = pd.read_sql(
            text("""SELECT game_id, market, line_value, pulled_at
                    FROM game_lines WHERE game_id = ANY(:g) ORDER BY pulled_at"""),
            conn, params={"g": game_ids})

    status = dict(zip(games["game_id"], games["status"]))
    pstats_lookup = {(r.player_id, r.game_id, r.stat_type): r.value for r in pstats.itertuples()}
    stat_to_market = {"runs_inning_1": "first_inning_runs", "runs_f5": "f5_runs"}
    tstats_lookup = {(int(r.game_id), stat_to_market[r.stat_type]): float(r.total)
                     for r in tstats.itertuples()}
    plines_grp = plines.groupby(["player_id", "game_id", "stat_type"])
    glines_grp = glines.groupby(["game_id", "market"])

    inserted = 0
    with engine.begin() as conn:
        for parlay_id, created_at, legs in parsed:
            results, odds_list, audit, ready = [], [], [], True
            for leg in legs:
                gid = int(leg["game_id"])
                if status.get(gid) != "FT":
                    ready = False; break

                key = builder_leg_key(leg)
                if key[0] == "player":
                    _, pid, _, stat_type = key
                    actual = pstats_lookup.get((pid, gid, stat_type))
                    try:
                        snaps = plines_grp.get_group((pid, gid, stat_type))
                    except KeyError:
                        ready = False; break
                    audit_id = {"player_id": pid, "stat_type": stat_type}
                else:
                    _, _, market = key
                    actual = tstats_lookup.get((gid, market))
                    try:
                        snaps = glines_grp.get_group((gid, market))
                    except KeyError:
                        ready = False; break
                    audit_id = {"market": market}

                if actual is None or pd.isna(actual):
                    ready = False; break
                line_value = _rec_snapshot(snaps, created_at)["line_value"]
                if line_value is None or pd.isna(line_value):
                    ready = False; break

                res = settle_leg(leg["side"], float(actual), float(line_value))
                results.append(res)
                odds_list.append(american_to_decimal(leg["odds"]))
                audit.append({**audit_id, "kind": leg["kind"], "game_id": gid,
                              "side": leg["side"], "line": float(line_value),
                              "odds": int(leg["odds"]), "actual": float(actual),
                              "result": res})
            if not ready:
                continue
            result, decimal_odds, pnl = parlay_result(results, odds_list)
            conn.execute(
                text(
                    """
                    INSERT INTO recommendation_outcomes
                        (bet_type, parlay_id, result, n_legs, stake, decimal_odds, pnl, legs, recommended_at)
                    VALUES ('parlay', :pid, :res, :n, 1, :co, :pnl, CAST(:legs AS JSONB), :ra)
                    """
                ),
                {"pid": int(parlay_id), "res": result, "n": len(legs),
                 "co": float(decimal_odds), "pnl": float(pnl),
                 "legs": json.dumps(audit), "ra": created_at})
            inserted += 1
    print(f"settle: settled {inserted} new builder parlays ({len(parsed) - inserted} not yet ready)")
    return inserted
```

- [ ] **Step 4: Wire it into `settle()`**

In `modeling/settle.py`, find the `settle(...)` function near the end and add a call to `settle_builder_parlays(engine)` alongside the existing `settle_parlays` / `settle_team_parlays` / `settle_edges` calls, following the same pattern and summing its return into the total.

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_settle_builder.py tests/test_settle.py -v
```
Expected: PASS — the 6 new cases plus all 21 pre-existing settle cases still green.

- [ ] **Step 6: Commit**

```bash
git add modeling/settle.py tests/test_settle_builder.py
git commit -m "feat(settle): score mixed player+team builder parlays"
```

---

### Task 5: API endpoint `GET /parlay-builder`

**Files:**
- Modify: `api/schemas.py`, `api/main.py`

**Interfaces:**
- Consumes: `optimizer.builder.{load_legs}`, `optimizer.builder_core.{build, cap_candidates, DEFAULT_FLOOR, DEFAULT_MAX_LEGS, DEFAULT_MIN_LEGS, DEFAULT_TOLERANCE}`.
- Produces: `BuilderLegOut`, `BuilderParlayOut`, route `GET /parlay-builder`.

**Contract rule:** additive only. Do **not** modify `/edges`, `/parlay-recommendations`, `/game-predictions`, `/box-scores`, `/games` — Budgerr consumes them (README §7.1).

- [ ] **Step 1: Add the schemas**

Append to `api/schemas.py`:

```python
class BuilderLegOut(BaseModel):
    game_id: int
    kind: str
    label: str
    player_id: int | None = None
    stat_type: str | None = None
    market: str | None = None
    side: str
    line: float
    odds: int
    market_prob: float
    # Shown for context only — never used to rank or filter (README §15.3).
    model_prob: float | None = None


class BuilderParlayOut(BaseModel):
    legs: list[BuilderLegOut]
    combined_odds: float
    joint_prob: float
    n_legs: int
```

- [ ] **Step 2: Add the route**

Append to `api/main.py` (match the existing route style and the global API-key dependency):

```python
@app.get("/parlay-builder", response_model=list[BuilderParlayOut])
def parlay_builder(
    target_payout: float | None = None,
    min_prob: float | None = None,
    tolerance: float = builder_core.DEFAULT_TOLERANCE,
    floor: float = builder_core.DEFAULT_FLOOR,
    min_legs: int = builder_core.DEFAULT_MIN_LEGS,
    max_legs: int = builder_core.DEFAULT_MAX_LEGS,
    top_n: int = 10,
):
    """Low-risk parlay constructions ranked by de-vigged MARKET probability.

    Pin target_payout and/or min_prob. joint_prob is the honest probability the
    whole parlay hits. No edge or expected-value claim is made or returned.
    """
    if target_payout is None and min_prob is None:
        raise HTTPException(
            status_code=422,
            detail="pin at least one axis: target_payout and/or min_prob",
        )
    if max_legs < min_legs:
        raise HTTPException(status_code=422, detail="max_legs must be >= min_legs")

    legs = builder.load_legs(get_engine(), floor)
    if not legs:
        return []
    legs = builder_core.cap_candidates(legs, max_legs)
    results = builder_core.build(
        legs, target_payout=target_payout, tolerance=tolerance, min_prob=min_prob,
        min_legs=min_legs, max_legs=max_legs, top_n=top_n,
    )
    return [
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
```

Add the imports at the top of `api/main.py`: `from optimizer import builder, builder_core` and `BuilderLegOut, BuilderParlayOut` to the existing `api.schemas` import. Use the module's existing engine accessor — match how neighbouring routes obtain their engine rather than inventing `get_engine()` if it differs.

- [ ] **Step 3: Verify on a SPARE port (never the live :8000)**

```bash
.venv/bin/uvicorn api.main:app --port 8099 &
sleep 4
curl -s "http://127.0.0.1:8099/parlay-builder" | head -c 300; echo
curl -s "http://127.0.0.1:8099/parlay-builder?target_payout=2.0&top_n=2" | head -c 600; echo
curl -s "http://127.0.0.1:8099/parlay-builder?min_prob=0.7&top_n=2" | head -c 600; echo
kill %1
```
Expected: the first call returns a 422 with the "pin at least one axis" detail; the other two return a JSON list (possibly empty if no live lines) with `joint_prob`, `combined_odds`, and per-leg `market_prob`. If `AUTH_ENABLED=true` in `.env`, add `-H "X-API-Key: <key from .env>"`.

- [ ] **Step 4: Confirm the Budgerr contract is untouched**

```bash
git diff --stat api/main.py api/schemas.py
git diff api/main.py | grep -E '^-' | grep -vE '^---' | head
```
Expected: additions only — no deleted lines in the existing `/edges`, `/parlay-recommendations`, `/game-predictions`, `/box-scores`, or `/games` handlers.

- [ ] **Step 5: Run the full suite and commit**

```bash
.venv/bin/pytest -q
git add api/main.py api/schemas.py
git commit -m "feat(api): GET /parlay-builder (additive, market-ranked)"
```
Expected: all tests pass (143 pre-existing + the new cases).

---

## Out of scope for Stage 1

- **Dashboard page** — Stage 2 (README §15.6).
- **Daily-chain swap** replacing `optimizer.parlay` with `optimizer.builder` in `scripts/daily_chain.sh` — the architect's reserved lane (production surface, README §15.7). Do not edit `scripts/daily_chain.sh` or any launchd plist.
- **Same-game combos** and **line shopping** — README §15.9 future work.

## Self-Review Notes

- Spec coverage: §15.4 favorite-side devig → Task 1; §15.5 search/cap/persistence → Tasks 2–3; §15.6 API → Task 5; §15.7 paper tracking → Task 4 (**gap found in the spec and closed here**: mixed-leg settlement is new code, contrary to §15.7's "no new settlement code" claim); §15.8 tests → Tasks 1, 2, 4.
- Type consistency: leg dict keys defined in Task 1 are used verbatim in Tasks 2, 3, 5; JSONB leg keys written in Task 3 (`kind`, `player_id`, `stat_type`, `market`, `side`, `odds`) are exactly the keys `builder_leg_key` reads in Task 4.
