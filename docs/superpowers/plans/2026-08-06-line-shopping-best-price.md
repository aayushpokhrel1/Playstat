# Line Shopping / Best-Price Legs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist SGO's discarded per-book (`byBookmaker`) prices so the builder pays each leg at the best available price at fixed risk, and show which book to place it at.

**Architecture:** Consensus two-sided devig still defines `market_prob` (ranking / 0.55 floor / joint_prob — unchanged). Ingestion additionally records the best single-book price + book for each side into new nullable columns; the builder swaps the chosen favorite side's payout price to the best one (falling back to consensus when absent). Additive-only end to end.

**Tech Stack:** Python 3.11, SQLAlchemy Core + psycopg2, PostgreSQL, pandas (leg loading), FastAPI + Pydantic, Next.js 16 + TypeScript, pytest.

## Global Constraints

- **Guardrails (§15.8, binding):** rank/floor on **consensus** devig `market_prob ≥ 0.55`; 2–4 legs; across-game only; paper-only; **no "+EV"/"edge"/"value"/"beat the market" language** anywhere (UI, API, JSONB); no signal-green except the existing ≥0.75 joint-prob rule.
- **Additive-only + mlb-default:** never change existing `prop_lines`/`game_lines` columns or the shapes Budgerr/settle rely on; new columns are nullable; new API/JSONB fields are optional with defaults. Budgerr reads `/parlay-builder/saved`, `/games`, `/box-scores` over HTTP (no shared DB).
- **Best price definition:** among a side's `byBookmaker` entries that are `available: true` AND quote the **exact** consensus line, pick the one with the **max `american_to_decimal(odds)`**. Else `(None, None)`.
- **v1 scope:** shop **`ou` markets only** (all live MLB player props + NRFI/F5 + full-game totals). Home/away (`sp`/`ml`) columns are created but left `NULL` in v1 (no live sport uses them; validated at NFL/NBA go-live). NULL best columns ⇒ consensus fallback, exactly like `model_prob=None`.
- **Repo rules:** run `graphify query "<question>"` before reading/grepping source (or read directly in a worktree — the graph is gitignored there). Main-checkout venv interpreter: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python`. No test DB — `ingestion.db.get_engine()` is LIVE; new tests must be pure or use the fake-engine/queue pattern in `tests/test_parlay_builder_api.py`. Never run `--save`/ingest against the live DB from a test.
- **Reserved lanes (architect only, not delegated):** the live migration (Task 1), live ingest/dry-run + `launchctl kickstart -k gui/$(id -u)/com.playstat.api` after any API-imported change (`optimizer/builder.py`, `optimizer/builder_core.py`), git push, browser verification. `ingestion/odds_ingest.py` is NOT API-imported.

---

### Task 1: Additive migration — best-price columns (ARCHITECT-run, live DB)

**Files:**
- Create: `db/migrations/009_line_shopping_best_price.sql`

**Interfaces:**
- Produces: nullable columns `prop_lines.{best_over_odds INT, best_over_book TEXT, best_under_odds INT, best_under_book TEXT}`; `game_lines.{best_over_odds, best_over_book, best_under_odds, best_under_book, best_home_odds, best_home_book, best_away_odds, best_away_book}` (INT/TEXT alternating). Consumed by Tasks 2 (writes) and 4 (reads).

- [ ] **Step 1: Write the migration SQL**

Create `db/migrations/009_line_shopping_best_price.sql`:

```sql
-- Line shopping (README §15.9 item 3): best single-book price + book per leg
-- side, kept ALONGSIDE the existing consensus over/under/home/away columns.
-- Additive + nullable: old rows and any line with no eligible book stay NULL,
-- and the builder falls back to the consensus price (like model_prob=None).
-- v1 populates only the over/under (ou) columns; the home/away (sp/ml) columns
-- are created for forward-compat but left NULL until NFL/NBA line shopping.
ALTER TABLE prop_lines
  ADD COLUMN best_over_odds  INTEGER,
  ADD COLUMN best_over_book  TEXT,
  ADD COLUMN best_under_odds INTEGER,
  ADD COLUMN best_under_book TEXT;

ALTER TABLE game_lines
  ADD COLUMN best_over_odds  INTEGER,
  ADD COLUMN best_over_book  TEXT,
  ADD COLUMN best_under_odds INTEGER,
  ADD COLUMN best_under_book TEXT,
  ADD COLUMN best_home_odds  INTEGER,
  ADD COLUMN best_home_book  TEXT,
  ADD COLUMN best_away_odds  INTEGER,
  ADD COLUMN best_away_book  TEXT;
```

- [ ] **Step 2: Back up the two table schemas, then apply (architect)**

```bash
cd /Users/aayushpokhrel/dev/playstat
pg_dump "$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)" --schema-only -t prop_lines -t game_lines \
  > "$SCRATCH/prop_game_lines_schema_pre009.sql"   # $SCRATCH = architect scratchpad
psql "$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)" -f db/migrations/009_line_shopping_best_price.sql
```

Expected: two `ALTER TABLE` lines, no error.

- [ ] **Step 3: Verify columns exist**

```bash
psql "$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)" -c \
 "SELECT table_name, column_name FROM information_schema.columns
  WHERE column_name LIKE 'best_%' ORDER BY table_name, ordinal_position;"
```

Expected: 4 `prop_lines` + 8 `game_lines` `best_*` columns.

- [ ] **Step 4: Commit**

```bash
git add db/migrations/009_line_shopping_best_price.sql
git commit -m "feat(db): additive best-price columns for line shopping (§15.9 item 3)"
```

---

### Task 2: Ingestion — `best_price` helper + populate rows (pure; delegatable)

**Files:**
- Modify: `ingestion/odds_ingest.py` (add `best_price`; extend `collect_prop_rows`, `collect_game_rows`, and the two `INSERT`s)
- Test: `tests/test_line_shopping.py` (new)

**Interfaces:**
- Consumes: `optimizer.parlay.american_to_decimal` (for max-decimal comparison).
- Produces: `best_price(by_bookmaker: dict | None, line_field: str | None, consensus_line) -> tuple[int | None, str | None]`. `collect_prop_rows` rows gain `best_over_odds/best_over_book/best_under_odds/best_under_book`; `collect_game_rows` ou rows gain the same four. Consumed by Task 4's SELECT and Task 3's normalizers (via the row dict).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_line_shopping.py`:

```python
from ingestion.odds_ingest import best_price, collect_prop_rows


def _bb(entries):
    return {bk: e for bk, e in entries.items()}


def test_best_price_picks_max_decimal_among_available_same_line():
    bb = {
        "draftkings": {"odds": "-120", "overUnder": "0.5", "available": True},
        "fanduel":    {"odds": "-105", "overUnder": "0.5", "available": True},  # best (least juice)
        "betmgm":     {"odds": "-130", "overUnder": "0.5", "available": True},
    }
    assert best_price(bb, "overUnder", 0.5) == (-105, "fanduel")


def test_best_price_positive_beats_negative():
    bb = {
        "a": {"odds": "+120", "overUnder": "1.5", "available": True},  # best payout
        "b": {"odds": "-105", "overUnder": "1.5", "available": True},
    }
    assert best_price(bb, "overUnder", 1.5) == (120, "a")


def test_best_price_skips_unavailable():
    bb = {
        "a": {"odds": "+200", "overUnder": "0.5", "available": False},  # ignored
        "b": {"odds": "-110", "overUnder": "0.5", "available": True},
    }
    assert best_price(bb, "overUnder", 0.5) == (-110, "b")


def test_best_price_skips_line_mismatch_exact_only():
    bb = {
        "a": {"odds": "+150", "overUnder": "2.5", "available": True},  # different line -> excluded
        "b": {"odds": "-110", "overUnder": "0.5", "available": True},
    }
    assert best_price(bb, "overUnder", 0.5) == (-110, "b")


def test_best_price_none_when_empty_or_no_eligible():
    assert best_price(None, "overUnder", 0.5) == (None, None)
    assert best_price({}, "overUnder", 0.5) == (None, None)
    assert best_price({"a": {"odds": "-110", "overUnder": "9.5", "available": True}},
                      "overUnder", 0.5) == (None, None)


def test_best_price_moneyline_no_line_matches_all_available():
    bb = {"a": {"odds": "-150", "available": True}, "b": {"odds": "-140", "available": True}}
    assert best_price(bb, None, None) == (-140, "b")


def test_collect_prop_rows_attaches_best_over_and_under():
    event = {
        "players": {"P1": {"name": "Player One"}},
        "odds": {
            "o1": {"statID": "batting_hits", "periodID": "game", "betTypeID": "ou",
                   "statEntityID": "P1", "sideID": "over", "bookOverUnder": "0.5",
                   "bookOdds": "-115",
                   "byBookmaker": {"dk": {"odds": "-110", "overUnder": "0.5", "available": True},
                                   "fd": {"odds": "-108", "overUnder": "0.5", "available": True}}},
            "u1": {"statID": "batting_hits", "periodID": "game", "betTypeID": "ou",
                   "statEntityID": "P1", "sideID": "under", "bookOverUnder": "0.5",
                   "bookOdds": "-105",
                   "byBookmaker": {"dk": {"odds": "-102", "overUnder": "0.5", "available": True}}},
        },
    }
    rows = collect_prop_rows(event, {"batting_hits": "hits"})
    assert len(rows) == 1
    r = rows[0]
    assert r["over_odds"] == -115 and r["under_odds"] == -105          # consensus unchanged
    assert r["best_over_odds"] == -108 and r["best_over_book"] == "fd"  # shopped
    assert r["best_under_odds"] == -102 and r["best_under_book"] == "dk"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_line_shopping.py -q`
Expected: FAIL — `ImportError: cannot import name 'best_price'`.

- [ ] **Step 3: Implement `best_price` + wire the collectors**

In `ingestion/odds_ingest.py`, add near the top (after the existing `parse_american_odds`):

```python
from optimizer.parlay import american_to_decimal


def best_price(by_bookmaker, line_field, consensus_line):
    """Best single-book price for one side (README §15.9 item 3, line shopping).

    Among `by_bookmaker` entries that are available AND quote the EXACT
    consensus line, return (american_odds, bookmaker_id) with the highest
    decimal odds (best payout, sign-agnostic). line_field is the per-book field
    holding the line ("overUnder" for ou, "spread" for sp); None means the
    market has no line (moneyline) so every available book is eligible.
    Returns (None, None) when nothing qualifies — the builder then falls back to
    the consensus price for that side.
    """
    if not by_bookmaker:
        return None, None
    best_dec = best_odds = best_book = None
    for book, entry in by_bookmaker.items():
        if not entry.get("available"):
            continue
        if line_field is not None:
            raw = entry.get(line_field)
            if raw is None or float(raw) != float(consensus_line):
                continue
        odds = parse_american_odds(entry.get("odds"))
        if odds is None:
            continue
        dec = american_to_decimal(odds)
        if best_dec is None or dec > best_dec:
            best_dec, best_odds, best_book = dec, odds, book
    return best_odds, best_book
```

In `collect_prop_rows`, inside the loop, after `row[f"{side}_odds"] = parse_american_odds(odd.get("bookOdds"))`, add:

```python
        b_odds, b_book = best_price(odd.get("byBookmaker"), "overUnder", odd.get("bookOverUnder"))
        row[f"best_{side}_odds"] = b_odds
        row[f"best_{side}_book"] = b_book
```

In `collect_game_rows`, in the `if want_bt == "ou":` branch, after `row[f"{side}_odds"] = price`, add:

```python
                b_odds, b_book = best_price(odd.get("byBookmaker"), "overUnder", odd.get("bookOverUnder"))
                row[f"best_{side}_odds"] = b_odds
                row[f"best_{side}_book"] = b_book
```

(Leave the `else` / home-away branch unchanged — sp/ml best-price is deferred to NFL/NBA go-live; those columns stay NULL in v1.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_line_shopping.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Extend the two INSERTs to write the new columns**

In `ingestion/odds_ingest.py:ingest_odds`, replace the `game_lines` INSERT with (adds the four ou best columns; home/away best columns stay NULL in v1):

```python
                conn.execute(
                    text(
                        "INSERT INTO game_lines (game_id, market, line_value, over_odds, under_odds, home_odds, away_odds, "
                        "best_over_odds, best_over_book, best_under_odds, best_under_book) "
                        "VALUES (:game_id, :market, :line_value, :over_odds, :under_odds, :home_odds, :away_odds, "
                        ":best_over_odds, :best_over_book, :best_under_odds, :best_under_book)"
                    ),
                    {
                        "game_id": game_id, "market": row["market"],
                        "line_value": row.get("line_value"),
                        "over_odds": row.get("over_odds"), "under_odds": row.get("under_odds"),
                        "home_odds": row.get("home_odds"), "away_odds": row.get("away_odds"),
                        "best_over_odds": row.get("best_over_odds"), "best_over_book": row.get("best_over_book"),
                        "best_under_odds": row.get("best_under_odds"), "best_under_book": row.get("best_under_book"),
                    },
                )
```

And replace the `prop_lines` INSERT with:

```python
                conn.execute(
                    text(
                        "INSERT INTO prop_lines "
                        "(player_id, game_id, stat_type, line_value, over_odds, under_odds, "
                        "best_over_odds, best_over_book, best_under_odds, best_under_book) "
                        "VALUES (:player_id, :game_id, :stat_type, :line_value, :over_odds, :under_odds, "
                        ":best_over_odds, :best_over_book, :best_under_odds, :best_under_book)"
                    ),
                    {
                        "player_id": player_id,
                        "game_id": game_id,
                        "stat_type": row["stat_type"],
                        "line_value": row.get("line_value"),
                        "over_odds": row.get("over_odds"),
                        "under_odds": row.get("under_odds"),
                        "best_over_odds": row.get("best_over_odds"), "best_over_book": row.get("best_over_book"),
                        "best_under_odds": row.get("best_under_odds"), "best_under_book": row.get("best_under_book"),
                    },
                )
```

- [ ] **Step 6: Run the full suite (imports + no regressions)**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest -q`
Expected: PASS (305 + 7 new = 312).

- [ ] **Step 7: Commit**

```bash
git add ingestion/odds_ingest.py tests/test_line_shopping.py
git commit -m "feat(ingestion): extract best-price per leg from SGO byBookmaker (§15.9 item 3)"
```

---

### Task 3: Builder core — pay the chosen side at best price (pure; delegatable)

**Files:**
- Modify: `optimizer/builder_core.py` (`_base_leg`, `normalize_player_leg`, `normalize_team_leg`; add `shopped_odds`)
- Test: `tests/test_builder_core.py`

**Interfaces:**
- Consumes: row dicts carrying `best_over_odds/best_over_book/best_under_odds/best_under_book` (Task 2), and for team rows optionally `best_home_*/best_away_*`.
- Produces: `shopped_odds(row, side) -> tuple[int, str | None]`; every leg dict now carries `"book"` (the winning bookmaker or `None`). `_base_leg(..., book=None)`. Consumed by Task 4 (`save_builds`) and Task 5 (API).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_builder_core.py`:

```python
def test_player_leg_uses_best_price_for_chosen_side_prob_stays_consensus():
    # consensus -200/+170 -> favorite OVER, market_prob from consensus devig.
    # best over price is -150 at fanduel -> payout uses -150, prob unchanged.
    consensus = normalize_player_leg({
        "game_id": 1, "player_id": 9, "stat_type": "hits", "line_value": 0.5,
        "player_name": "P", "over_odds": -200, "under_odds": 170,
        "best_over_odds": None, "best_over_book": None,
        "best_under_odds": None, "best_under_book": None, "model_prob": None,
    })
    shopped = normalize_player_leg({
        "game_id": 1, "player_id": 9, "stat_type": "hits", "line_value": 0.5,
        "player_name": "P", "over_odds": -200, "under_odds": 170,
        "best_over_odds": -150, "best_over_book": "fanduel",
        "best_under_odds": None, "best_under_book": None, "model_prob": None,
    })
    assert shopped["side"] == "over" == consensus["side"]
    assert shopped["market_prob"] == consensus["market_prob"]        # ranking unchanged
    assert shopped["american_odds"] == -150 and shopped["book"] == "fanduel"
    assert shopped["decimal_odds"] > consensus["decimal_odds"]       # bigger payout
    assert consensus["book"] is None                                 # fallback


def test_player_leg_falls_back_to_consensus_when_no_best_for_chosen_side():
    # favorite is UNDER (+? ); only best_over present -> under uses consensus.
    leg = normalize_player_leg({
        "game_id": 2, "player_id": 3, "stat_type": "hits", "line_value": 0.5,
        "player_name": "Q", "over_odds": 170, "under_odds": -200,
        "best_over_odds": -150, "best_over_book": "dk",   # wrong side, ignored
        "best_under_odds": None, "best_under_book": None, "model_prob": None,
    })
    assert leg["side"] == "under" and leg["american_odds"] == -200 and leg["book"] is None


def test_team_ou_leg_uses_best_price():
    leg = normalize_team_leg({
        "game_id": 4, "market": "first_inning_runs", "line_value": 0.5,
        "over_odds": None, "under_odds": None, "home_odds": None, "away_odds": None,
        # NRFI is under-favored here:
        "over_odds": 150, "under_odds": -180,
        "best_over_odds": None, "best_over_book": None,
        "best_under_odds": -160, "best_under_book": "betmgm", "model_prob": None,
    })
    assert leg["side"] == "under" and leg["american_odds"] == -160 and leg["book"] == "betmgm"


def test_team_homeaway_leg_has_book_none_in_v1():
    leg = normalize_team_leg({
        "game_id": 5, "market": "full_game_moneyline", "line_value": None,
        "over_odds": None, "under_odds": None, "home_odds": -250, "away_odds": 200,
        "model_prob": None,
    })
    assert leg["side"] == "home" and leg["american_odds"] == -250 and leg["book"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder_core.py -q -k "best_price or best or book or fall_back or homeaway_leg_has_book"`
Expected: FAIL — `KeyError: 'book'` (leg dict has no "book" yet).

- [ ] **Step 3: Implement**

In `optimizer/builder_core.py`, add the `book` param to `_base_leg`:

```python
def _base_leg(game_id, side, market_prob, line_value, american_odds, model_prob, label, book=None):
    return {
        "game_id": int(game_id),
        "label": label,
        "side": side,
        "line_value": None if line_value is None else float(line_value),
        "american_odds": int(american_odds),
        "decimal_odds": american_to_decimal(int(american_odds)),
        "market_prob": float(market_prob),
        "model_prob": _clean_optional(model_prob),
        "book": book,
    }
```

Add a helper (below `_base_leg`):

```python
# side -> (best-odds column, best-book column, consensus-odds column) for the
# shopped payout price. market_prob is ALWAYS the consensus devig (ranking/floor
# unchanged); only the payout price is shopped. A missing best_* (NULL / absent)
# falls back to the consensus price for that side (README §15.9 item 3).
_SHOP_COLS = {
    "over":  ("best_over_odds",  "best_over_book",  "over_odds"),
    "under": ("best_under_odds", "best_under_book", "under_odds"),
    "home":  ("best_home_odds",  "best_home_book",  "home_odds"),
    "away":  ("best_away_odds",  "best_away_book",  "away_odds"),
}


def shopped_odds(row, side):
    """(american_odds, book) for the chosen side: the best single-book price when
    present, else the consensus price (book None)."""
    best_col, book_col, cons_col = _SHOP_COLS[side]
    best = row.get(best_col)
    if best is not None:
        return int(best), row.get(book_col)
    return int(row[cons_col]), None
```

Rewrite `normalize_player_leg`:

```python
def normalize_player_leg(row):
    side, prob = favorite_side(row["over_odds"], row["under_odds"])
    odds, book = shopped_odds(row, side)
    label = f"{row.get('player_name', 'player')} {row['stat_type']} {side} {row['line_value']}"
    leg = _base_leg(row["game_id"], side, prob, row["line_value"], odds,
                    row.get("model_prob"), label, book=book)
    leg.update({"kind": "player", "player_id": int(row["player_id"]),
                "stat_type": row["stat_type"], "market": None})
    return leg
```

Rewrite `normalize_team_leg`:

```python
def normalize_team_leg(row):
    market = row["market"]
    if is_home_away_market(market):
        raw, prob = favorite_side(row["home_odds"], row["away_odds"])   # "over"->home, "under"->away
        side = "home" if raw == "over" else "away"
        odds, book = shopped_odds(row, side)
        line = row.get("line_value")
        label = f"{market} {side}" if line is None else f"{market} {side} {line}"
    else:
        side, prob = favorite_side(row["over_odds"], row["under_odds"])
        odds, book = shopped_odds(row, side)
        line = row["line_value"]
        label = f"{market} {side} {line}"
    leg = _base_leg(row["game_id"], side, prob, line, odds, row.get("model_prob"), label, book=book)
    leg.update({"kind": "team", "player_id": None, "stat_type": None, "market": market})
    return leg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder_core.py -q`
Expected: PASS (existing team/player tests still pass — their rows have no `best_*` keys, so `shopped_odds` falls back to consensus and matches prior `american_odds`; new tests pass).

- [ ] **Step 5: Run the exactness oracle (must be untouched)**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder_search_exactness.py -q`
Expected: PASS — `build()` is unchanged; it consumes `decimal_odds`/`market_prob` exactly as before.

- [ ] **Step 6: Commit**

```bash
git add optimizer/builder_core.py tests/test_builder_core.py
git commit -m "feat(builder): pay chosen side at best-shopped price, keep consensus prob (§15.9 item 3)"
```

---

### Task 4: Builder loader SELECT + persist `book` (delegatable; SQL — review carefully)

**Files:**
- Modify: `optimizer/builder.py` (`load_player_legs` SELECT, `load_team_legs` SELECT, `save_builds` legs_json)
- Test: `tests/test_builder.py`

**Interfaces:**
- Consumes: DB columns from Task 1; `shopped_odds`/leg `"book"` from Task 3.
- Produces: saved legs JSONB entries carry `"book"`. Consumed by Task 5 (API reads `leg.get("book")`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_builder.py` (uses the existing `_CapturingEngine`/fake pattern in that file — mirror the closest existing `save_builds` test's setup):

```python
def test_save_builds_writes_book_into_legs_json():
    import json
    from optimizer.builder import save_builds
    eng = _CapturingEngine(existing_legs=[])   # same fake used by other save_builds tests
    results = [{
        "legs": [{
            "kind": "player", "game_id": 1, "player_id": 9, "stat_type": "hits",
            "market": None, "side": "over", "american_odds": -150, "line_value": 0.5,
            "label": "P hits over 0.5", "market_prob": 0.66, "model_prob": None,
            "book": "fanduel",
        }],
        "joint_prob": 0.66, "combined_odds": 1.67, "n_legs": 1,
    }]
    save_builds(eng, 1.4, results, "across_game", "mlb")
    saved = json.loads(eng.last_insert_params["legs"])
    assert saved["legs"][0]["book"] == "fanduel"
```

(If `_CapturingEngine`'s constructor/attribute names differ, match this file's existing `save_builds` test exactly — do not invent a new fake.)

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder.py::test_save_builds_writes_book_into_legs_json -q`
Expected: FAIL — `KeyError: 'book'` in the legs_json dict comprehension.

- [ ] **Step 3: Add `book` to `save_builds` legs_json**

In `optimizer/builder.py:save_builds`, in the `legs_json = [ {...} for leg in r["legs"] ]` comprehension, add `"book": leg["book"],` (e.g. right after the `"model_prob": leg["model_prob"],` line).

- [ ] **Step 4: Extend the two loader SELECTs**

In `load_player_legs`, change the outer and inner SELECT column lists to include the best columns:

```sql
                SELECT pl.player_id, pl.game_id, pl.stat_type, pl.line_value,
                       pl.over_odds, pl.under_odds, p.name AS player_name,
                       pl.best_over_odds, pl.best_over_book,
                       pl.best_under_odds, pl.best_under_book
                FROM (
                    SELECT DISTINCT ON (player_id, game_id, stat_type)
                        player_id, game_id, stat_type, line_value, over_odds, under_odds,
                        best_over_odds, best_over_book, best_under_odds, best_under_book
                    FROM prop_lines
                    ORDER BY player_id, game_id, stat_type, pulled_at DESC
                ) pl
                JOIN games g ON g.game_id = pl.game_id AND g.status != 'FT'
                    AND g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)
                                   AND COALESCE(:slate_date, CURRENT_DATE) + :window_days
                    AND g.sport = :sport
                JOIN players p ON p.player_id = pl.player_id
```

In `load_team_legs`, likewise:

```sql
                SELECT gl.game_id, gl.market, gl.line_value,
                       gl.over_odds, gl.under_odds, gl.home_odds, gl.away_odds,
                       gl.best_over_odds, gl.best_over_book,
                       gl.best_under_odds, gl.best_under_book,
                       gl.best_home_odds, gl.best_home_book,
                       gl.best_away_odds, gl.best_away_book
                FROM (
                    SELECT DISTINCT ON (game_id, market)
                        game_id, market, line_value, over_odds, under_odds, home_odds, away_odds,
                        best_over_odds, best_over_book, best_under_odds, best_under_book,
                        best_home_odds, best_home_book, best_away_odds, best_away_book
                    FROM game_lines
                    WHERE market = ANY(:markets)
                    ORDER BY game_id, market, pulled_at DESC
                ) gl
                JOIN games g ON g.game_id = gl.game_id AND g.status != 'FT'
                    AND g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)
                                   AND COALESCE(:slate_date, CURRENT_DATE) + :window_days
                    AND g.sport = :sport
```

(`_normalize` already coerces pandas NaN→None per-cell, so absent best_* arrive as None and `shopped_odds` falls back.)

- [ ] **Step 5: Run tests**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_builder.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add optimizer/builder.py tests/test_builder.py
git commit -m "feat(builder): load best-price columns + persist book in saved legs (§15.9 item 3)"
```

---

### Task 5: API — expose `book` on builder legs (delegatable)

**Files:**
- Modify: `api/schemas.py` (`BuilderLegOut`)
- Modify: `api/main.py` (both leg-construction sites: `/parlay-builder` search ~L362, `/parlay-builder/saved` ~L459)
- Test: `tests/test_parlay_builder_api.py`

**Interfaces:**
- Consumes: leg dicts (search) and legs JSONB (saved), each carrying `book` (Tasks 3/4). Legacy saved rows predate `book` → `leg.get("book")` yields `None`.
- Produces: `BuilderLegOut.book: str | None = None`. Consumed by Task 6 (web).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parlay_builder_api.py` (reuse the file's existing fake-engine/queue fixtures — the saved-endpoint test that feeds a legs blob is the template):

```python
def test_saved_builder_leg_exposes_book(client, fake_engine):
    # Feed one builder row whose legs blob carries a "book"; assert it round-trips.
    fake_engine.queue_saved_builder_row(legs=[{
        "kind": "player", "game_id": 1, "player_id": 9, "stat_type": "hits",
        "market": None, "side": "over", "odds": -150, "line": 0.5,
        "label": "P hits over 0.5", "market_prob": 0.66, "model_prob": None,
        "book": "fanduel",
    }])
    resp = client.get("/parlay-builder/saved?tier=all&limit=1")
    assert resp.status_code == 200
    assert resp.json()[0]["legs"][0]["book"] == "fanduel"


def test_saved_builder_leg_book_defaults_none_for_legacy_rows(client, fake_engine):
    # A legacy blob with no "book" key still validates, book -> None.
    fake_engine.queue_saved_builder_row(legs=[{
        "kind": "player", "game_id": 1, "player_id": 9, "stat_type": "hits",
        "market": None, "side": "over", "odds": -150, "line": 0.5,
        "label": "P hits over 0.5", "market_prob": 0.66, "model_prob": None,
    }])
    resp = client.get("/parlay-builder/saved?tier=all&limit=1")
    assert resp.status_code == 200
    assert resp.json()[0]["legs"][0]["book"] is None
```

(Match the actual fixture/helper names in `tests/test_parlay_builder_api.py`; if a `queue_saved_builder_row`-style helper doesn't exist, build the row using the same construction the existing saved test uses, just adding/removing the `"book"` key.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_parlay_builder_api.py -q -k book`
Expected: FAIL — response leg has no `book` key.

- [ ] **Step 3: Add the schema field**

In `api/schemas.py:BuilderLegOut`, add after `model_prob`:

```python
    # Best single-book price's bookmaker for this leg's shopped odds (line
    # shopping, README §15.9 item 3). None when unshopped (consensus price) or
    # for legacy rows predating the field. Additive/defaulted — Budgerr-safe.
    book: str | None = None
```

- [ ] **Step 4: Populate at both sites**

In `api/main.py` `/parlay-builder` search construction (~L362-368), add `book=leg.get("book"),` to the `BuilderLegOut(...)` call. In `/parlay-builder/saved` construction (~L459-465), add `book=leg.get("book"),` likewise.

- [ ] **Step 5: Run tests**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_parlay_builder_api.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py api/main.py tests/test_parlay_builder_api.py
git commit -m "feat(api): expose book on builder legs (§15.9 item 3)"
```

---

### Task 6: Dashboard — show the book on each leg (delegatable; Next 16 — read docs)

**Files:**
- Modify: `web/app/lib/api.ts` (`BuilderLeg` type)
- Modify: `web/app/builder/ConstructionList.tsx` (render the book)

**Interfaces:**
- Consumes: `BuilderLegOut.book` from Task 5.
- Produces: rendered book label. Terminal (no downstream consumer).

- [ ] **Step 1: Read the Next 16 caveat**

`web/AGENTS.md` requires reading `web/node_modules/next/dist/docs/` guides before writing Next code. This task is a pure presentational change to an existing client component — no new Next APIs — but confirm no App-Router change is needed.

- [ ] **Step 2: Add `book` to the TS type**

In `web/app/lib/api.ts`, in the `BuilderLeg` type, add after `model_prob`:

```typescript
  book: string | null;
```

- [ ] **Step 3: Render the book next to the price**

In `web/app/builder/ConstructionList.tsx`, in the leg-data block, change the odds line from:

```tsx
                  <span>{formatOdds(leg.odds)}</span>
```

to:

```tsx
                  <span>
                    {formatOdds(leg.odds)}
                    {leg.book ? ` · ${leg.book}` : ""}
                  </span>
```

- [ ] **Step 4: Typecheck + build**

Run:
```bash
cd /Users/aayushpokhrel/dev/playstat/web && npx tsc --noEmit && npm run build
```
Expected: no type errors; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add web/app/lib/api.ts web/app/builder/ConstructionList.tsx
git commit -m "feat(web): show best-price book on builder legs (§15.9 item 3)"
```

---

### Task 7: README + live rollout & verification (ARCHITECT-run)

**Files:**
- Modify: `README.md` (§15.9 item 3 → mark BUILT; add a §15.10 build note)

- [ ] **Step 1: Run graphify update**

```bash
cd /Users/aayushpokhrel/dev/playstat && graphify update .
```

- [ ] **Step 2: Deploy ingestion + reconfirm SGO fields (dry-run), then real ingest**

Ingestion isn't API-imported. When SGO quota is available:
```bash
/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m ingestion.odds_ingest --sport mlb --dry-run
```
Expected: events > 0, mapped statIDs populated (confirms `byBookmaker`/`overUnder` field names). Then a real ingest (`--sport mlb`, no flag) or wait for the 08:31 chain to populate `best_*`.

- [ ] **Step 3: Kickstart the API (optimizer/builder.py is API-imported)**

```bash
launchctl kickstart -k gui/$(id -u)/com.playstat.api
```

- [ ] **Step 4: Live verify (read-only)**

- A shopped MLB card's `combined_odds` ≥ its consensus-only value at equal `joint_prob` (compare a `/parlay-builder` result before/after ingest, or spot-check one leg's `best_over_odds` vs `over_odds` in the DB).
- Each shopped leg carries a non-null `book`; unshopped legs are `null`.
- `GET /parlay-builder`, `GET /parlay-builder/saved?tier=all` → 200; the saved response's existing fields are byte-unchanged for Budgerr (only the additive `book` appears).
- Dashboard builder page renders "· <book>" beside shopped prices (browser, behind login).

- [ ] **Step 5: Update README + commit + push**

Mark §15.9 item 3 **BUILT & DEPLOYED 2026-08-06** and add a §15.10 note: consensus-prob-for-ranking + best-book-price-for-payout; additive columns (migration 009); v1 shops ou markets only (sp/ml deferred); Budgerr byte-unchanged; N pytest green; live-verified.

```bash
git add README.md && git commit -m "docs(README §15.9/§15.10): line shopping BUILT (§15.9 item 3)" && git push origin main
```

---

## Self-Review

**Spec coverage:** ingestion best-price extraction (Task 2) ✓; consensus-prob/best-payout split (Task 3) ✓; additive schema incl. book (Task 1) ✓; builder loader + persist book (Task 4) ✓; API additive book (Task 5) ✓; dashboard book (Task 6) ✓; settle unchanged (no task — verified: reads odds from JSONB) ✓; guardrails (Global Constraints + each task) ✓; exact-line/available/max-decimal (Task 2 helper + tests) ✓; per-side consensus fallback (Task 3 tests) ✓; sp/ml best-effort deferral (Global Constraints + Tasks 1/2/3) ✓; rollout/kickstart (Task 7) ✓.

**Type consistency:** `best_price(by_bookmaker, line_field, consensus_line)` used consistently (Tasks 2). `shopped_odds(row, side)` + `_SHOP_COLS` keys `over/under/home/away` match `_base_leg(..., book=None)` and leg `"book"` used in Tasks 3/4/5/6. Column names `best_{over,under,home,away}_{odds,book}` identical across migration (1), INSERTs/SELECTs (2/4), and `_SHOP_COLS` (3). `BuilderLegOut.book: str | None = None` (5) ↔ `BuilderLeg.book: string | null` (6).

**Placeholders:** none — all code shown. Two tasks (4, 5) note "match the existing fake-engine helper names" because the fakes live in those test files; the row shapes are given in full.
