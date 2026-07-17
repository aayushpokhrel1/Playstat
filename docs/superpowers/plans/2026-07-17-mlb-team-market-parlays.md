# MLB Team-Market Parlays (NRFI + F5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pivot MLB betting from biased player props to two team game-total markets — 1st-inning runs (NRFI) and first-5-innings runs (F5) — with a safest-path-to-2x parlay builder that carries an honest model-vs-market EV tag.

**Architecture:** F5 outcomes reconstruct for free from the StatsAPI linescores we already fetch; a new `modeling/f5.py` mirrors the proven `modeling/first_inning.py` classifier+regressor pattern. Game-level lines and predictions (which today are served but never turned into edges) get a new `game_edges` computation and a new `optimizer/team_parlay.py` builder that produces both across-game parlays (independent product) and separately-labeled same-game NRFI+F5 pairs (empirical co-occurrence, since the innings are nested). Player props are hard-stopped, not deleted.

**Tech Stack:** Python 3.11, XGBoost 3.2, pandas, SQLAlchemy + psycopg2, PostgreSQL 14.22, pytest; Next.js 16 dashboard.

## Global Constraints

- **Design spec:** `docs/superpowers/specs/2026-07-17-mlb-team-market-parlays-design.md` — every task serves it.
- **graphify first:** run `graphify query "<question>"` before reading/grepping source; `graphify update .` after modifying code (AST-only, free). Applies to every task and every subagent.
- **Architect-only lanes (NEVER done by a subagent):** DB migrations against the live `playstat` DB; any run of a module that writes live tables (`f5.py`, `team_edges.py`, `team_parlay.py`, `odds_ingest.py`, `settle.py`) against the live DB; launchd/`scripts/daily_chain.sh` changes and the `com.playstat.mlb` swap; the `com.playstat.api` restart (`launchctl kickstart -k gui/$(id -u)/com.playstat.api`); SGO API calls (credentials + quota); `git push`; the Budgerr coordination message.
- **Subagents develop and test only against a throwaway DB and a spare uvicorn port** — never the live `playstat` DB, never `:8000`. Reads of the live DB are fine; writes are not. Subagents commit in their worktree; the architect merges.
- **Worktree hygiene:** copy `.env` (and `web/.env.local` for web tasks) from the main checkout into the worktree; `npm install` in `web/` for web tasks. Main venv: `/Users/aayushpokhrel/dev/playstat/.venv`.
- **Budgerr contract:** `/edges` and `/parlay-recommendations` are being redefined around team markets *by agreement* (Budgerr isn't consuming yet). Additive-only no longer binds these two, but coordinate the final shape (Task 12) before the API restart.
- **Per-sport ID offsets** unchanged (nba +0, mlb +100M, nfl +200M). No new IDs are minted by this plan.
- **Tests are pure-math/DB-free where possible** — a root `conftest.py` sets dummy `DATABASE_URL`/`API_BASKETBALL_KEY`, so `pytest -q` runs with no `.env`.

---

## Phase 0 — F5 line availability (research gate)

### Task 0: Probe SGO for the F5 period ID

**ARCHITECT-ONLY** (SGO credentials + quota). Determines whether the F5 leg is fully priced/EV-tagged, priced-on-a-paid-tier, or model-only. Blocks Task 6's EV tag and Task 8's F5 line ingestion, nothing else.

**Files:**
- Modify (after the probe): `docs/superpowers/specs/2026-07-17-mlb-team-market-parlays-design.md` (record the finding), `ingestion/odds_ingest.py:45-49` (add the F5 `GAME_MARKETS` entry only if a free-tier period ID is found).

- [ ] **Step 1: Probe one event's odds for F5-flavored markets**

Run (architect, from repo root, `.venv` active):
```bash
python - <<'PY'
from ingestion.odds_client import SportsGameOddsClient
from ingestion.config import SPORTS
c = SportsGameOddsClient()
seen = {}
for ev in c.get_events(SPORTS["mlb"]["odds_league_id"], odds_available=True):
    for o in ev.get("odds", {}).values():
        pid = o.get("periodID")
        if pid and pid != "game":
            seen.setdefault(pid, set()).add((o.get("statID"), o.get("statEntityID")))
    break  # one event is enough; SGO bills per event
for pid, combos in sorted(seen.items()):
    print(pid, sorted(combos)[:6])
PY
```
Expected: a list of non-`game` period IDs. `1i` is first-inning. Look for an F5 period — likely `1_5i`, `f5`, `1h`, or `firstFiveInnings` — paired with `("points","all")`.

- [ ] **Step 2: Record the outcome in the spec and branch the plan**

- **Free-tier F5 period found** → add to `ingestion/odds_ingest.py`:
  ```python
  GAME_MARKETS = {
      "mlb": {
          "first_inning_runs": ("points", "all", "1i"),
          "f5_runs": ("points", "all", "<F5_PERIOD_ID>"),
      },
  }
  ```
  Commit that one-line change with the probe result in the message. Tasks 6/8 proceed fully.
- **F5 present but paid-tier only** (odds carry the "missing N bookmaker odds" upsell / no free quote) → do NOT add the entry. F5 stays model-only: Task 6 emits F5 legs with `odds=None` and no EV tag; escalate the SGO Rookie ($99/mo, §14.2) decision to the user.
- **No F5 period at all** → F5 model-only as above; note to revisit the market with the user.

- [ ] **Step 3: Commit the spec update**
```bash
git add docs/superpowers/specs/2026-07-17-mlb-team-market-parlays-design.md ingestion/odds_ingest.py
git commit -m "Phase 0: record SGO F5 line probe result (<free|paid|absent>)"
```

---

## Phase 1 — Data layer

### Task 1: Migration — F5 outcomes, game edges, parlay kind

**Files:**
- Create: `db/migrations/NNN_team_markets_f5.sql` (architect assigns `NNN` at apply time: `006` if the held line-shopping `005` lands first, else `005`).

**Interfaces:**
- Produces: `team_game_stats` gains stat_type rows `runs_f5`; `parlay_recommendations.kind` (text, default `'player'`); new table `game_edges(game_id, market, side, model_prob, implied_prob, edge, created_at)`.

> Note: `team_game_stats` is long-format (`player_id?`/`team_id`/`game_id`/`stat_type`/`value`) — F5 is a new `stat_type='runs_f5'`, no column add there. Only `parlay_recommendations.kind` and the `game_edges` table are DDL.

- [ ] **Step 1: Write the migration SQL**

Create `db/migrations/NNN_team_markets_f5.sql`:
```sql
-- Team-market parlays (NRFI + F5). See docs/superpowers/specs/2026-07-17-mlb-team-market-parlays-design.md
BEGIN;

-- Discriminator so the team pipeline and legacy player parlays share one table + ledger.
ALTER TABLE parlay_recommendations
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'player';

-- Game-level edges: today game_lines/game_predictions are served but never turned
-- into edges (edges is player-keyed). This is the team-market analogue of `edges`.
CREATE TABLE IF NOT EXISTS game_edges (
    game_id       integer NOT NULL REFERENCES games(game_id),
    market        text    NOT NULL,
    side          text    NOT NULL,
    model_prob    numeric,
    implied_prob  numeric,
    edge          numeric,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (game_id, market)
);
CREATE INDEX IF NOT EXISTS idx_game_edges_market ON game_edges (market);

COMMIT;
```

- [ ] **Step 2: Subagent verifies the migration on a throwaway DB**
```bash
createdb playstat_teamtest
psql -d playstat_teamtest -f db/migrations/NNN_team_markets_f5.sql
psql -d playstat_teamtest -c "\d game_edges" -c "\d parlay_recommendations" | grep -E "kind|game_edges|market"
psql -d playstat_teamtest -f db/migrations/NNN_team_markets_f5.sql   # idempotent re-run
dropdb playstat_teamtest
```
Expected: both runs succeed (IF NOT EXISTS makes it idempotent); `kind` column and `game_edges` table present.

- [ ] **Step 3: Commit**
```bash
git add db/migrations/NNN_team_markets_f5.sql
git commit -m "Phase 1: migration for F5 outcomes, game_edges, parlay kind"
```

- [ ] **Step 4: ARCHITECT applies to the live DB** (not the subagent): `psql -d playstat -f db/migrations/NNN_team_markets_f5.sql`, then spot-checks `\d game_edges`.

### Task 2: Reconstruct F5 runs in the linescore loop

**Files:**
- Modify: `ingestion/mlb_backfill.py:223-230` (the `_upsert...` linescore parse).
- Test: `tests/test_f5_reconstruct.py`

**Interfaces:**
- Consumes: the StatsAPI `linescore.innings` list already fetched (each item has `home.runs`/`away.runs`).
- Produces: `team_game_stats` rows with `stat_type='runs_f5'`, value = sum of innings 1–5 for that side. Pure helper `f5_runs(innings, side) -> int | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_f5_reconstruct.py`:
```python
from ingestion.mlb_backfill import f5_runs


def test_f5_sums_first_five_innings():
    innings = [{"home": {"runs": r}} for r in (1, 0, 2, 0, 1, 3, 0)]  # 7 innings
    assert f5_runs(innings, "home") == 4  # 1+0+2+0+1, ignores innings 6-7


def test_f5_short_game_sums_what_exists():
    innings = [{"away": {"runs": r}} for r in (2, 1, 0)]  # rain-shortened, 3 innings
    assert f5_runs(innings, "away") == 3


def test_f5_missing_side_treated_as_zero():
    innings = [{"home": {"runs": 1}}, {}, {"home": {"runs": 2}}]
    assert f5_runs(innings, "home") == 3  # missing inning-2 home -> 0


def test_f5_no_innings_returns_none():
    assert f5_runs([], "home") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_f5_reconstruct.py -v`
Expected: FAIL, `ImportError: cannot import name 'f5_runs'`.

- [ ] **Step 3: Add the helper and wire it into the loop**

In `ingestion/mlb_backfill.py`, add near the top-level helpers:
```python
def f5_runs(innings, side):
    """Sum a side's runs over innings 1-5 (fewer if the game was short). None if
    no innings at all — mirrors runs_inning_1/runs, which are also None pre-game."""
    if not innings:
        return None
    return sum((inn.get(side) or {}).get("runs") or 0 for inn in innings[:5])
```
Then, in the linescore parse where `runs_inning_1` and `runs` are set (currently `mlb_backfill.py:227-230`), add the F5 line inside the same `if is_final and innings:` block:
```python
                if is_final and innings:
                    first = innings[0].get(side) or {}
                    stats["runs_inning_1"] = first.get("runs")
                    stats["runs"] = sum((inn.get(side) or {}).get("runs") or 0 for inn in innings)
                    stats["runs_f5"] = f5_runs(innings, side)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_f5_reconstruct.py -v`
Expected: 4 passed.

- [ ] **Step 5: Subagent validates against a throwaway DB slice**

Confirm the writer emits `runs_f5` and it is internally consistent (F5 ≤ full-game runs for the same side/game). On a throwaway copy DB only:
```bash
python -m ingestion.mlb_backfill --only linescores   # DATABASE_URL pointed at the throwaway copy
psql -d <throwaway> -c "SELECT COUNT(*) FILTER (WHERE stat_type='runs_f5') runs_f5, COUNT(*) FILTER (WHERE stat_type='runs') runs FROM team_game_stats;"
psql -d <throwaway> -c "
  SELECT COUNT(*) AS violations FROM team_game_stats a JOIN team_game_stats b
  USING (game_id, team_id) WHERE a.stat_type='runs_f5' AND b.stat_type='runs' AND a.value > b.value;"
```
Expected: `runs_f5` count ≈ `runs` count; `violations = 0`.

- [ ] **Step 6: Commit**
```bash
git add ingestion/mlb_backfill.py tests/test_f5_reconstruct.py
git commit -m "Phase 1: reconstruct F5 (first-5-innings) runs from linescores"
```

- [ ] **Step 7: ARCHITECT backfills live**: `python -m ingestion.mlb_backfill --only linescores` against live (one hydrated StatsAPI request per season, free), then re-checks the two queries above on `playstat`.

### Task 3: Empirical NRFI×F5 correlation

**Files:**
- Create: `modeling/correlation.py`
- Test: `tests/test_correlation.py`

**Interfaces:**
- Produces:
  - `pair_joint_prob(p_a, p_b, lift) -> float` — dependence-adjusted joint probability of two concordant/among team-market legs on the *same game*, clamped to `[0, min(p_a, p_b)]`.
  - `empirical_lift(both, a, b, n) -> float` — observed/expected ratio from counts, `1.0` when either marginal is empty.
  - `nrfi_f5_lift(engine, side_nrfi, side_f5, f5_line=4.5) -> tuple[float, int]` — `(lift, n_games)` from historical `team_game_stats` (reads only).

- [ ] **Step 1: Write the failing test**

Create `tests/test_correlation.py`:
```python
import pytest
from modeling.correlation import pair_joint_prob, empirical_lift


def test_lift_independence_is_one():
    # both-under = 0.25, marginals 0.5 and 0.5 over n=100 -> exactly independent
    assert empirical_lift(both=25, a=50, b=50, n=100) == pytest.approx(1.0)


def test_lift_positive_dependence_above_one():
    # both-under observed 40 vs expected 25 -> lift 1.6
    assert empirical_lift(both=40, a=50, b=50, n=100) == pytest.approx(1.6)


def test_lift_empty_marginal_defaults_to_one():
    assert empirical_lift(both=0, a=0, b=10, n=100) == 1.0


def test_pair_joint_applies_lift():
    assert pair_joint_prob(0.6, 0.5, lift=1.5) == pytest.approx(0.45)


def test_pair_joint_clamped_to_min_marginal():
    # 0.9*0.9*2.0 = 1.62 -> clamped to min(0.9,0.9)=0.9
    assert pair_joint_prob(0.9, 0.9, lift=2.0) == 0.9


def test_pair_joint_clamped_nonnegative():
    assert pair_joint_prob(0.3, 0.3, lift=0.0) == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_correlation.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'modeling.correlation'`.

- [ ] **Step 3: Implement**

Create `modeling/correlation.py`:
```python
"""Empirical NRFI x F5 co-occurrence for same-game team-market pairs.

The 1st inning is nested inside innings 1-5 and positively correlated, so a
same-game (NRFI, F5) pair must NOT use naive P_a * P_b. v1 corrects the product
by a global observed/expected "lift" measured from box-score history — auditable,
no new modeling family. Known bias: assumes constant dependence and is noisy
until ~a season of shared history exists (README §14.2 correlation notes).
"""

import pandas as pd
from sqlalchemy import text


def empirical_lift(both, a, b, n):
    """observed P(both) / (P(a) * P(b)) from raw counts; 1.0 if either marginal empty."""
    if n == 0 or a == 0 or b == 0:
        return 1.0
    expected = (a / n) * (b / n)
    return (both / n) / expected


def pair_joint_prob(p_a, p_b, lift):
    """Dependence-adjusted joint prob of two same-game legs, clamped to a valid range."""
    joint = p_a * p_b * lift
    return max(0.0, min(joint, min(p_a, p_b)))


def _game_totals(engine, f5_line):
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT game_id,
                       SUM(value) FILTER (WHERE stat_type='runs_inning_1') AS fi,
                       SUM(value) FILTER (WHERE stat_type='runs_f5')       AS f5
                FROM team_game_stats
                GROUP BY game_id
                """
            ),
            conn,
        )
    return df.dropna(subset=["fi", "f5"])


def nrfi_f5_lift(engine, side_nrfi, side_f5, f5_line=4.5, nrfi_line=1.5):
    """(lift, n_games) for a same-game NRFI-side x F5-side pair from history."""
    df = _game_totals(engine, f5_line)
    n = len(df)
    nrfi_hit = (df["fi"] < nrfi_line) if side_nrfi == "under" else (df["fi"] > nrfi_line)
    f5_hit = (df["f5"] < f5_line) if side_f5 == "under" else (df["f5"] > f5_line)
    return empirical_lift(int((nrfi_hit & f5_hit).sum()), int(nrfi_hit.sum()), int(f5_hit.sum()), n), n
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_correlation.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**
```bash
git add modeling/correlation.py tests/test_correlation.py
git commit -m "Phase 1: empirical NRFI x F5 co-occurrence (lift) for same-game pairs"
```

---

## Phase 2 — F5 model

### Task 4: F5 total-runs model

**Files:**
- Create: `modeling/f5.py`
- Test: `tests/test_f5_model.py`

**Interfaces:**
- Consumes: `team_game_stats` `runs_f5` (Task 2), starter form (same source as `first_inning.py`).
- Produces: `game_predictions` rows with `market='f5_runs'`, `model_version='xgb_f5_v1'`; pure helper `prob_under_line_poisson(mean, line) -> float`. Reuses the `first_inning.py` feature-builder pattern verbatim (team rolling form + starter form) — only the stat column (`runs_f5`) and market constants differ.

> F5 ≈ "the starters' game" — innings 1–5 are mostly the two starting pitchers, so the starter-form features `first_inning.py` already builds are directly on-point. Target mean ~4.5 (far less zero-inflated than NRFI), so the classifier has more signal to work with. Report holdout Brier vs the always-base-rate baseline exactly as `first_inning.py` does — do NOT assert an edge.

- [ ] **Step 1: Write the failing test (pure helper only; the model itself is validated by its holdout print)**

Create `tests/test_f5_model.py`:
```python
import pytest
from modeling.f5 import prob_under_line_poisson


def test_prob_under_line_poisson_matches_scipy():
    from scipy.stats import poisson
    mean, line = 4.5, 4.5
    # P(X < 4.5) = P(X <= 4) = poisson.cdf(4, 4.5)
    assert prob_under_line_poisson(mean, line) == pytest.approx(poisson.cdf(4, mean))


def test_prob_under_line_poisson_half_integer_line():
    from scipy.stats import poisson
    assert prob_under_line_poisson(5.0, 3.5) == pytest.approx(poisson.cdf(3, 5.0))


def test_prob_under_line_poisson_monotonic_in_mean():
    assert prob_under_line_poisson(3.0, 4.5) > prob_under_line_poisson(6.0, 4.5)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_f5_model.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'modeling.f5'`.

- [ ] **Step 3: Implement `modeling/f5.py`**

Copy `modeling/first_inning.py` to `modeling/f5.py` and change exactly these things (leave `_add_team_form`, `_add_starter_form`, and the fit/holdout structure identical — they are stat-agnostic given the renamed columns):
- Constants block:
  ```python
  MARKET = "f5_runs"
  LINE = 4.5          # books' typical F5 total; predicted_mean lets any line be priced
  MODEL_VERSION = "xgb_f5_v1"
  ```
- In `_load_game_frame`, change the joined stat from `runs_inning_1` to `runs_f5`, and rename the selected columns `home_fi`/`away_fi` → `home_f5`/`away_f5` throughout the module (feature names `*_scored_fi`/`*_allowed_fi` may stay — they are just labels — but rename to `*_f5` for clarity).
- Replace `prob_under_2` with a line-parameterized Poisson helper (F5's line isn't 1.5):
  ```python
  from scipy.stats import poisson

  def prob_under_line_poisson(mean, line):
      """P(total < line) for total ~ Poisson(mean). Kept only for a sanity mean;
      the traded probability is the classifier's, as in first_inning.py."""
      import math
      mean = max(float(mean), 1e-6)
      k = math.ceil(line) - 1          # P(X < line) = P(X <= ceil(line)-1)
      return float(poisson.cdf(k, mean))
  ```
- The classifier target becomes `(total_f5 < LINE)`; keep the direct-classifier approach (do not derive prob from the Poisson mean) — same rationale as NRFI (overdispersion). Keep the exact holdout Brier-vs-base-rate print.
- Upsert into `game_predictions` with the new `MARKET`/`MODEL_VERSION`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_f5_model.py -v`
Expected: 3 passed.

- [ ] **Step 5: Subagent runs the holdout on a read-only copy and reports the number**

Point `DATABASE_URL` at a read-only live copy (or the live DB read-only — this module reads history then writes `game_predictions`; on the copy the write is harmless). Run `python -m modeling.f5 --days 2` and capture the `holdout n=...: ... Brier X (always-predict-base-rate Brier: Y)` line. **Report X vs Y in the task hand-back.** If X ≥ Y (no better than base rate), say so plainly — that is a finding, not a failure; the builder still ships (layered goal), but it feeds the honesty note in Task 11.

- [ ] **Step 6: Commit**
```bash
git add modeling/f5.py tests/test_f5_model.py
git commit -m "Phase 2: F5 (first-5-innings) total-runs model, holdout X vs base Y"
```

- [ ] **Step 7: ARCHITECT runs `python -m modeling.f5 --days 2` live** to populate `game_predictions` for upcoming games.

---

## Phase 3 — Edges, optimizer, settlement

### Task 5: Team (game-market) edge computation

**Files:**
- Create: `modeling/team_edges.py`
- Test: `tests/test_team_edges.py`

**Interfaces:**
- Consumes: `devig`, `odds_to_probability` from `modeling/edges.py`; `game_lines` (snapshot-per-pull) × `game_predictions` (`prob_over`/`prob_under`).
- Produces: `game_edges` rows (Task 1 table); pure helper `best_side(model_p_over, model_p_under, implied_over, implied_under) -> (side, model_prob, implied_prob, edge)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_team_edges.py`:
```python
import pytest
from modeling.team_edges import best_side


def test_best_side_picks_larger_edge():
    # model 0.75 under vs devig implied 0.60 under -> under edge 0.15 wins
    side, mp, ip, edge = best_side(0.25, 0.75, 0.40, 0.60)
    assert side == "under"
    assert edge == pytest.approx(0.15)
    assert mp == pytest.approx(0.75)


def test_best_side_over_when_over_edge_larger():
    side, mp, ip, edge = best_side(0.70, 0.30, 0.50, 0.50)
    assert side == "over"
    assert edge == pytest.approx(0.20)


def test_best_side_ties_go_to_over():
    side, *_ = best_side(0.5, 0.5, 0.4, 0.4)
    assert side == "over"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_team_edges.py -v`
Expected: FAIL, import error.

- [ ] **Step 3: Implement**

Create `modeling/team_edges.py`:
```python
"""Game-market edges (NRFI, F5): game_lines x game_predictions -> game_edges.

The team-market analogue of modeling/edges.py. game-level markets were served
(game_predictions) but never turned into edges/parlays before this. Same devig
math as edges.py; keyed by (game_id, market) instead of (player, game, stat).
"""

import pandas as pd
from sqlalchemy import text

from ingestion import db
from modeling.edges import devig


def best_side(model_p_over, model_p_under, implied_over, implied_under):
    edge_over = model_p_over - implied_over
    edge_under = model_p_under - implied_under
    if edge_over >= edge_under:
        return "over", model_p_over, implied_over, edge_over
    return "under", model_p_under, implied_under, edge_under


def _latest_game_lines(conn):
    return pd.read_sql(
        text(
            """
            SELECT DISTINCT ON (game_id, market)
                game_id, market, line_value, over_odds, under_odds
            FROM game_lines
            ORDER BY game_id, market, pulled_at DESC
            """
        ),
        conn,
    )


def compute_team_edges(engine):
    with engine.begin() as conn:
        lines = _latest_game_lines(conn)
        if lines.empty:
            print("team_edges: game_lines empty — nothing to compute yet.")
            return
        preds = pd.read_sql(
            text(
                """
                SELECT game_id, market, prob_over, prob_under
                FROM game_predictions
                WHERE prob_over IS NOT NULL AND prob_under IS NOT NULL
                """
            ),
            conn,
        )

    merged = lines.merge(preds, on=["game_id", "market"], how="inner")
    rows, skipped = 0, 0
    fresh = []
    with engine.begin() as conn:
        for r in merged.to_dict("records"):
            if pd.isna(r["over_odds"]) or pd.isna(r["under_odds"]):
                skipped += 1
                continue
            implied_over, implied_under = devig(r["over_odds"], r["under_odds"])
            side, mp, ip, edge = best_side(
                float(r["prob_over"]), float(r["prob_under"]), implied_over, implied_under
            )
            db.upsert(
                conn, "game_edges", ["game_id", "market"],
                {"game_id": int(r["game_id"]), "market": r["market"],
                 "side": side, "model_prob": float(mp),
                 "implied_prob": float(ip), "edge": float(edge)},
            )
            rows += 1
            fresh.append((int(r["game_id"]), r["market"]))

        # Prune stale unplayed-game edges (same rationale as edges.py).
        stale = conn.execute(
            text(
                """
                DELETE FROM game_edges ge USING games g
                WHERE g.game_id = ge.game_id AND g.status != 'FT'
                  AND NOT EXISTS (
                      SELECT 1 FROM unnest(CAST(:gids AS bigint[]), CAST(:mkts AS text[])) AS f(game_id, market)
                      WHERE f.game_id = ge.game_id AND f.market = ge.market)
                """
            ),
            {"gids": [k[0] for k in fresh], "mkts": [k[1] for k in fresh]},
        ).rowcount if fresh else 0

    print(f"team_edges: upserted {rows} rows"
          + (f" (skipped {skipped} one-sided)" if skipped else "")
          + (f" (pruned {stale} stale)" if stale else ""))


if __name__ == "__main__":
    compute_team_edges(db.get_engine())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_edges.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**
```bash
git add modeling/team_edges.py tests/test_team_edges.py
git commit -m "Phase 3: game-market edges (NRFI/F5) -> game_edges"
```

### Task 6: Team-parlay builder

**Files:**
- Create: `optimizer/team_parlay.py`
- Test: `tests/test_team_parlay.py`

**Interfaces:**
- Consumes: `american_to_decimal`, `find_combinations` from `optimizer/parlay.py`; `pair_joint_prob`, `nrfi_f5_lift` from `modeling/correlation.py`; `devig` from `modeling/edges.py`; `game_edges` + latest `game_lines` + `game_predictions`.
- Produces: `parlay_recommendations` rows with `kind='team'`; leg JSONB shape `{game_id, market, side, odds, model_prob}`; each recommendation's `legs` carries an `ev` field (`joint_prob*combined_odds - 1`) and a `class` of `'across_game'` or `'same_game_pair'`. Pure helper `recommendation_ev(joint_prob, combined_odds) -> float`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_team_parlay.py`:
```python
import pytest
from optimizer.team_parlay import recommendation_ev, same_game_pairs


def test_ev_positive_when_model_beats_price():
    # joint 0.55 at combined decimal 2.0 -> 0.55*2 - 1 = 0.10
    assert recommendation_ev(0.55, 2.0) == pytest.approx(0.10)


def test_ev_negative_when_price_beats_model():
    assert recommendation_ev(0.45, 2.0) == pytest.approx(-0.10)


def test_same_game_pairs_uses_lift_not_naive_product():
    legs = [
        {"game_id": 1, "market": "first_inning_runs", "side": "under", "model_prob": 0.7, "decimal_odds": 1.4},
        {"game_id": 1, "market": "f5_runs", "side": "under", "model_prob": 0.55, "decimal_odds": 1.5},
    ]
    # lift 1.3 -> joint = 0.7*0.55*1.3 = 0.5005, NOT the naive 0.385
    out = same_game_pairs(legs, lift_fn=lambda sn, sf: (1.3, 500), target_payout=2.0, tolerance=0.15)
    assert len(out) == 1
    assert out[0]["joint_prob"] == pytest.approx(0.5005)
    assert out[0]["class"] == "same_game_pair"


def test_same_game_pairs_excludes_cross_game():
    legs = [
        {"game_id": 1, "market": "first_inning_runs", "side": "under", "model_prob": 0.7, "decimal_odds": 1.4},
        {"game_id": 2, "market": "f5_runs", "side": "under", "model_prob": 0.55, "decimal_odds": 1.5},
    ]
    assert same_game_pairs(legs, lift_fn=lambda sn, sf: (1.3, 500), target_payout=2.0, tolerance=0.15) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_team_parlay.py -v`
Expected: FAIL, import error.

- [ ] **Step 3: Implement**

Create `optimizer/team_parlay.py`:
```python
"""Team-market parlay builder (NRFI + F5). Two output classes, kept separate:
  * across_game: legs from DIFFERENT games -> independent joint = product.
  * same_game_pair: NRFI + F5 on the SAME game -> empirical lift-adjusted joint
    (the innings are nested; naive product is wrong).
Each recommendation carries an honest model-vs-market EV (README §14.1 layered goal).
"""

import argparse
import itertools
import json

import pandas as pd
from sqlalchemy import text

from ingestion import db
from optimizer.parlay import american_to_decimal, find_combinations
from modeling.correlation import pair_joint_prob, nrfi_f5_lift

TARGET_PAYOUT = 2.0
TOLERANCE = 0.15
MIN_LEGS, MAX_LEGS = 2, 3
TOP_N = 10


def recommendation_ev(joint_prob, combined_odds):
    return joint_prob * combined_odds - 1


def same_game_pairs(legs, lift_fn, target_payout, tolerance):
    """One 2-leg pair per game that has both a NRFI and an F5 leg."""
    out = []
    by_game = {}
    for leg in legs:
        by_game.setdefault(leg["game_id"], []).append(leg)
    for game_id, glegs in by_game.items():
        nrfi = next((l for l in glegs if l["market"] == "first_inning_runs"), None)
        f5 = next((l for l in glegs if l["market"] == "f5_runs"), None)
        if not nrfi or not f5:
            continue
        combined = nrfi["decimal_odds"] * f5["decimal_odds"]
        if abs(combined - target_payout) / target_payout > tolerance:
            continue
        lift, n = lift_fn(nrfi["side"], f5["side"])
        joint = pair_joint_prob(nrfi["model_prob"], f5["model_prob"], lift)
        out.append({"legs": [nrfi, f5], "combined_odds": combined, "joint_prob": joint,
                    "class": "same_game_pair", "lift": lift, "lift_n": n})
    out.sort(key=lambda m: m["joint_prob"], reverse=True)
    return out


def load_team_legs(engine, min_edge=0.0):
    """Candidate legs from game_edges joined to the latest game_lines odds and
    the model prob for the edge's side. min_edge=0 keeps the builder populated
    even without a real edge (layered goal); EV is tagged per recommendation."""
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT ge.game_id, ge.market, ge.side, ge.model_prob, ge.edge,
                       CASE ge.side WHEN 'over' THEN gl.over_odds ELSE gl.under_odds END AS odds
                FROM game_edges ge
                JOIN (
                    SELECT DISTINCT ON (game_id, market) game_id, market, over_odds, under_odds
                    FROM game_lines ORDER BY game_id, market, pulled_at DESC
                ) gl ON gl.game_id = ge.game_id AND gl.market = ge.market
                JOIN games g ON g.game_id = ge.game_id AND g.status != 'FT'
                WHERE ge.edge >= :min_edge
                """
            ),
            conn, params={"min_edge": min_edge},
        )
    if df.empty:
        return []
    df = df.dropna(subset=["odds"])
    df["decimal_odds"] = df["odds"].apply(american_to_decimal)
    return df.to_dict("records")


def save_team_recommendations(engine, matches, top_n):
    rows = 0
    with engine.begin() as conn:
        for m in matches[:top_n]:
            legs_json = [
                {"game_id": int(l["game_id"]), "market": l["market"], "side": l["side"],
                 "odds": int(l["odds"]), "model_prob": float(l["model_prob"])}
                for l in m["legs"]
            ]
            conn.execute(
                text(
                    """
                    INSERT INTO parlay_recommendations
                        (kind, target_payout, legs, joint_prob, combined_odds)
                    VALUES ('team', :tp, CAST(:legs AS JSONB), :jp, :co)
                    """
                ),
                {"tp": TARGET_PAYOUT, "legs": json.dumps(
                    {"class": m["class"], "ev": recommendation_ev(m["joint_prob"], m["combined_odds"]),
                     "legs": legs_json}),
                 "jp": m["joint_prob"], "co": m["combined_odds"]},
            )
            rows += 1
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-payout", type=float, default=TARGET_PAYOUT)
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    parser.add_argument("--max-legs", type=int, default=MAX_LEGS)
    parser.add_argument("--top-n", type=int, default=TOP_N)
    args = parser.parse_args()

    engine = db.get_engine()
    legs = load_team_legs(engine)
    print(f"team legs: {len(legs)}")
    if not legs:
        print("no team legs yet (expected until game_edges + game_lines have data).")
        return

    across = find_combinations(legs, args.target_payout, MIN_LEGS, args.max_legs, args.tolerance)
    for m in across:
        m["class"] = "across_game"

    lift_cache = {}
    def lift_fn(side_nrfi, side_f5):
        key = (side_nrfi, side_f5)
        if key not in lift_cache:
            lift_cache[key] = nrfi_f5_lift(engine, side_nrfi, side_f5)
        return lift_cache[key]

    pairs = same_game_pairs(legs, lift_fn, args.target_payout, args.tolerance)

    print(f"across-game combos: {len(across)}, same-game pairs: {len(pairs)}")
    saved = save_team_recommendations(engine, across, args.top_n) \
        + save_team_recommendations(engine, pairs, args.top_n)
    print(f"parlay_recommendations (kind=team): inserted {saved} rows")


if __name__ == "__main__":
    main()
```

> Note: `find_combinations` already excludes same-game combos, so `across` is automatically same-game-free; `same_game_pairs` is the only path that pairs within a game.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_team_parlay.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**
```bash
git add optimizer/team_parlay.py tests/test_team_parlay.py
git commit -m "Phase 3: team-parlay builder (across-game + same-game NRFI+F5 pairs) with EV tag"
```

### Task 7: Team-aware settlement

**Files:**
- Modify: `modeling/settle.py` (add `settle_team_parlays`, call it from `settle`).
- Test: `tests/test_settle_team.py`

**Interfaces:**
- Consumes: `settle_leg`, `parlay_result` from `modeling/settle.py` (already pure); team leg JSONB `{game_id, market, side, odds, model_prob}`; `team_game_stats` (`runs_inning_1`, `runs_f5`) + latest `game_lines` for the line at rec time.
- Produces: `recommendation_outcomes` rows with `bet_type='parlay'` for `kind='team'` parlays; pure helper `team_leg_actual(totals, game_id, market) -> float | None` mapping a market to its game total.

- [ ] **Step 1: Write the failing test**

Create `tests/test_settle_team.py`:
```python
import pytest
from modeling.settle import team_leg_actual, settle_leg, parlay_result


def test_team_leg_actual_maps_market_to_total():
    totals = {(1, "first_inning_runs"): 0.0, (1, "f5_runs"): 3.0}
    assert team_leg_actual(totals, 1, "first_inning_runs") == 0.0
    assert team_leg_actual(totals, 1, "f5_runs") == 3.0
    assert team_leg_actual(totals, 2, "f5_runs") is None


def test_team_pair_under_under_wins():
    # NRFI under 1.5 actual 0 -> hit; F5 under 4.5 actual 3 -> hit
    r1 = settle_leg("under", 0.0, 1.5)
    r2 = settle_leg("under", 3.0, 4.5)
    result, _, pnl = parlay_result([r1, r2], [1.4, 1.5])
    assert result == "win"
    assert pnl == pytest.approx(1.4 * 1.5 - 1)


def test_team_pair_one_miss_loses():
    r1 = settle_leg("under", 2.0, 1.5)   # miss
    r2 = settle_leg("under", 3.0, 4.5)   # hit
    result, _, pnl = parlay_result([r1, r2], [1.4, 1.5])
    assert result == "loss"
    assert pnl == -1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_settle_team.py -v`
Expected: FAIL, `cannot import name 'team_leg_actual'`.

- [ ] **Step 3: Implement**

In `modeling/settle.py`, add the pure helper and the settle function, and call it from `settle`:
```python
MARKET_TO_STAT = {"first_inning_runs": "runs_inning_1", "f5_runs": "runs_f5"}


def team_leg_actual(totals, game_id, market):
    """Game total for a team market: totals maps (game_id, market) -> summed runs."""
    return totals.get((game_id, market))


def settle_team_parlays(engine):
    with engine.begin() as conn:
        candidates = conn.execute(
            text(
                """
                SELECT pr.parlay_id, pr.created_at, pr.legs
                FROM parlay_recommendations pr
                WHERE pr.kind = 'team' AND NOT EXISTS (
                    SELECT 1 FROM recommendation_outcomes ro
                    WHERE ro.bet_type = 'parlay' AND ro.parlay_id = pr.parlay_id)
                """
            )
        ).fetchall()
        if not candidates:
            print("settle: no new team parlays to evaluate.")
            return 0

        parsed = [(pid, ca, _as_legs_list(raw)) for pid, ca, raw in candidates]
        # team legs live under a {"class","ev","legs":[...]} wrapper
        parsed = [(pid, ca, blob["legs"] if isinstance(blob, dict) else blob) for pid, ca, blob in parsed]
        game_ids = sorted({int(l["game_id"]) for _, _, legs in parsed for l in legs})

        games = pd.read_sql(text("SELECT game_id, status FROM games WHERE game_id = ANY(:g)"),
                            conn, params={"g": game_ids})
        team_totals = pd.read_sql(
            text(
                """
                SELECT game_id, stat_type, SUM(value) AS total
                FROM team_game_stats
                WHERE game_id = ANY(:g) AND stat_type IN ('runs_inning_1','runs_f5')
                GROUP BY game_id, stat_type
                """
            ),
            conn, params={"g": game_ids})
        lines = pd.read_sql(
            text(
                """
                SELECT game_id, market, line_value, pulled_at
                FROM game_lines WHERE game_id = ANY(:g) ORDER BY pulled_at
                """
            ),
            conn, params={"g": game_ids})

    status = dict(zip(games["game_id"], games["status"]))
    totals = {(int(r.game_id), {"runs_inning_1": "first_inning_runs", "runs_f5": "f5_runs"}[r.stat_type]):
              float(r.total) for r in team_totals.itertuples()}
    lines_grp = lines.groupby(["game_id", "market"])

    inserted = 0
    with engine.begin() as conn:
        for parlay_id, created_at, legs in parsed:
            results, odds_list, audit, ready = [], [], [], True
            for leg in legs:
                gid, market, side, odds = int(leg["game_id"]), leg["market"], leg["side"], leg["odds"]
                if status.get(gid) != "FT":
                    ready = False; break
                actual = team_leg_actual(totals, gid, market)
                if actual is None:
                    ready = False; break
                try:
                    snaps = lines_grp.get_group((gid, market))
                except KeyError:
                    ready = False; break
                rec_snap = _rec_snapshot(snaps, created_at)
                line_value = rec_snap["line_value"]
                if line_value is None or pd.isna(line_value):
                    ready = False; break
                res = settle_leg(side, float(actual), float(line_value))
                results.append(res)
                odds_list.append(american_to_decimal(odds))
                audit.append({"game_id": gid, "market": market, "side": side,
                              "line": float(line_value), "odds": int(odds),
                              "actual": float(actual), "result": res})
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
    print(f"settle: settled {inserted} new team parlays ({len(parsed) - inserted} not yet ready)")
    return inserted
```
And extend `settle`:
```python
def settle(engine):
    settle_parlays(engine)
    settle_team_parlays(engine)
    settle_edges(engine)
    print_summary(engine)
```

> `settle_parlays` filters implicitly to player parlays because team parlays store legs under a `{"class",...,"legs":[...]}` wrapper without `player_id`; guard `settle_parlays` to skip `kind='team'` rows by adding `AND pr.kind = 'player'` to its candidate query so the two never cross.

- [ ] **Step 4: Add the `kind='player'` guard to `settle_parlays`**

In `settle_parlays`'s candidate query, change `FROM parlay_recommendations pr WHERE NOT EXISTS (...)` to `FROM parlay_recommendations pr WHERE pr.kind = 'player' AND NOT EXISTS (...)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_settle_team.py tests/test_settle.py -v`
Expected: all pass (existing `test_settle.py` still green — the pure helpers are unchanged).

- [ ] **Step 6: Commit**
```bash
git add modeling/settle.py tests/test_settle_team.py
git commit -m "Phase 3: team-aware settlement (team_game_stats + game_lines)"
```

---

## Phase 4 — Serving, chain, coordination

### Task 8: API — team-parlay + edge endpoints

**Files:**
- Modify: `api/main.py`, `api/schemas.py`
- Test: `tests/test_api_team.py` (schema-shape unit test; full endpoint check is architect browser/`curl` verification)

**Interfaces:**
- Produces: `GET /team-parlay-recommendations?date=&class=` returning `[{parlay_id, created_at, class, ev, joint_prob, combined_odds, legs:[{game_id, market, side, odds, model_prob}]}]`; `GET /game-edges?date=` returning `game_edges` rows joined to team names. Both behind the existing global API-key dependency. `/edges` and `/parlay-recommendations` keep their shapes but the daily chain stops writing player rows to them (Task 10) — coordinate the redefinition with Budgerr (Task 12) before the restart.

- [ ] **Step 1: Add Pydantic schemas** in `api/schemas.py` (`TeamParlayOut`, `TeamParlayLeg`, `GameEdgeOut`) mirroring the existing `ParlayLeg`/`BacktestRunOut` style — fields exactly as in the Interfaces block above. Match the `web/app` camel/snake convention used by existing schemas (snake_case, `from_attributes`).

- [ ] **Step 2: Add the two read-only endpoints** in `api/main.py`, following the existing `/parlay-recommendations` and `/game-predictions` handlers (same `Depends(require_api_key)`, same JSONB `_as_legs_list` defensive parse for `legs`). The `class`/`ev` come from the leg-wrapper JSONB written in Task 6.

- [ ] **Step 3: Write a schema-shape test**

Create `tests/test_api_team.py` asserting `TeamParlayOut(**sample).legs[0].market == "f5_runs"` and that `ev` is a float — a pure Pydantic test needing no DB.

Run: `pytest tests/test_api_team.py -v` → PASS.

- [ ] **Step 4: Subagent smoke-tests on a spare port** (`uvicorn api.main:app --port 8099` against a throwaway DB seeded with one team parlay), `curl`s both endpoints, kills the server. Never `:8000`.

- [ ] **Step 5: Commit**
```bash
git add api/main.py api/schemas.py tests/test_api_team.py
git commit -m "Phase 4: /team-parlay-recommendations + /game-edges endpoints"
```

- [ ] **Step 6: ARCHITECT restarts the live API** after merge: `launchctl kickstart -k gui/$(id -u)/com.playstat.api`, then `curl -s -H "X-API-Key: <key>" localhost:8000/team-parlay-recommendations`.

### Task 9: Dashboard — team parlays in, player props out

**Files:**
- Create: `web/app/team-parlays/` (page + fetch), following `web/app/edges/` conventions.
- Modify: nav/links to remove player-prop edges/parlays entry points; `web/app/edges/EdgesExplorer.tsx` (hide player-prop view behind the shelf) — read PRODUCT.md + DESIGN.md first.
- Test: covered by architect browser verification.

**Interfaces:**
- Consumes: `/team-parlay-recommendations`, `/game-edges` (Task 8).

- [ ] **Step 1: Read PRODUCT.md, DESIGN.md, `web/AGENTS.md`, and the Next 16 docs in `web/node_modules/next/dist/docs/`** (the version differs from training data — `proxy.ts` not `middleware.ts`).
- [ ] **Step 2: Build the team-parlays page** — two sections (across-game, same-game pairs), each row showing legs (game, market, side, odds), joint prob, combined odds, and the EV tag styled in the one signal-green accent when positive / muted when not. Near-black terminal surface, Geist Sans/Mono. Match `web/app/edges/EdgesExplorer.tsx` table idioms.
- [ ] **Step 3: Remove the player-prop edges/parlays entry points** from the dashboard nav (hard stop per the spec). Keep the components in the tree (reversible), just unlinked.
- [ ] **Step 4: Commit**
```bash
git add web/app/team-parlays web/app/edges web/app/<nav files>
git commit -m "Phase 4: dashboard team-parlays view; shelve player-prop UI"
```
- [ ] **Step 5: ARCHITECT verifies in the browser preview** (login required; creds with the user), screenshots the team-parlays page, confirms player-prop links are gone.

### Task 10: Daily-chain swap

**ARCHITECT-ONLY** (launchd lane). Modify `scripts/daily_chain.sh`'s `run_chain()`: replace the player-prop `modeling.edges` and `optimizer.parlay` steps with the team pipeline, keeping settle (now team-aware) and the heartbeat wrapper.

- [ ] **Step 1** — new chain body (after `modeling.predict_upcoming`): `... && modeling.f5 --days 2 && odds_ingest --sport mlb && first_inning --days 2 && modeling.team_edges && optimizer.team_parlay && modeling.backtest --sport mlb && modeling.settle`. Drop `modeling.edges` + `optimizer.parlay` (player).
- [ ] **Step 2** — dry-run `scripts/daily_chain.sh` with `PLAYSTAT_CHAIN_CMD` stub (Task's smoke pattern) to confirm the wrapper still guards/ pings correctly, then a real foreground run once, watching `logs/mlb.log`.
- [ ] **Step 3** — commit `scripts/daily_chain.sh`.

### Task 11: README + honesty note

**ARCHITECT-ONLY.** Update in the same commit as the chain swap lands:
- §11: shrinkage-bias finding (slopes 0.18–0.85, calibrated-not-resolved) as the reason player props are shelved; F5 holdout Brier-vs-baseline from Task 4.
- §13: new "team-market parlays" build entry (data/model/optimizer/settlement).
- §14: mark §14.1 player-prop items superseded/shelved; add the team-market pipeline as the live MLB betting path; note F5-line availability outcome from Task 0.

### Task 12: Budgerr final contract message

**ARCHITECT-ONLY**, before the API restart. Send the Budgerr session the finalized `/edges` + `/parlay-recommendations` + `/team-parlay-recommendations` shapes and the team-market leg schema (`{game_id, market, side, odds}` settled from `team_game_stats`), replacing the player-prop contract they were told to hold on.

---

## Self-Review

**Spec coverage:** F5 outcomes (Task 2), F5 lines gate (Task 0), F5 model + honesty (Task 4), correlation table (Task 3), across-game + same-game builder (Task 6), EV tag (Task 6), team settlement (Task 7), reuse of parlay helpers (Tasks 6/7), `kind` discriminator (Tasks 1/6/7), API redefinition + Budgerr coordination (Tasks 8/12), dashboard add-team/remove-player (Task 9), chain swap (Task 10), README (Task 11), migration-number caveat (Task 1). All spec sections map to a task.

**Placeholder scan:** `NNN` (migration number) and `<F5_PERIOD_ID>` are deliberate gated values resolved by the architect at apply time / by Task 0 — not unfilled TODOs. UI/API tasks (8/9) intentionally specify shape + pattern-to-follow rather than full code because they mirror cited existing files (`web/app/edges/`, `/parlay-recommendations` handler) and are gated on architect browser/curl verification; the pure-math and model tasks (2–7) carry complete code and tests.

**Type consistency:** leg JSONB shape `{game_id, market, side, odds, model_prob}` is identical across Tasks 6 (write), 7 (settle), 8 (serve). `pair_joint_prob`/`empirical_lift`/`nrfi_f5_lift` signatures match between Task 3 (def) and Task 6 (use). `best_side` returns `(side, model_prob, implied_prob, edge)` consistently in Task 5. `team_leg_actual(totals, game_id, market)` matches between Task 7 def and test.
