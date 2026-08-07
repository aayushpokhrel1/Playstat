# Line shopping / best-price legs — Design Spec

**Date:** 2026-08-06
**Roadmap:** §15.9 item 3 (builder enhancements). The cheapest real win — no
modeling, no extra SGO quota, no user cost decision.
**Status:** user-confirmed design (2026-08-06); ready for `writing-plans`.

## Goal

The low-risk builder currently pays out at the **consensus** book price for each
leg. SportsGameOdds (SGO) already returns a `byBookmaker` breakdown per market
that `ingestion/odds_ingest.py` **discards** — it keeps only the consensus
`bookOdds`/`bookOverUnder` it writes to `prop_lines`/`game_lines`. Persisting the
**best available price per leg** lets the builder pay out more at **fixed risk**:
same legs, same safety, a strictly-better-or-equal payout, placeable at a named
book.

## The core judgement — de-vigging vs best price (user-confirmed)

The builder ranks legs on `devig(over_odds, under_odds)` market probability
(`optimizer/builder_core.py:favorite_side`, via `optimizer/devig.py`). The best
over-price and best under-price can come from **different books**. Decision:

- **Ranking / floor / joint probability stay on the CONSENSUS two-sided devig.**
  `market_prob` is unchanged. The consensus two-sided price is the
  best-calibrated probability we have (§15.2), and it is the honest one: a
  cross-book "no-vig" (each side's best-book price) mixes two books' vig
  structures, understates the true vig, **inflates apparent safety**, and is not
  placeable as a single bet — it would violate the honesty core (§15.2 / §15.8
  guardrail 1 & 3). **Rejected.**
- **The best single-book price for the CHOSEN (favorite) side feeds the PAYOUT.**
  `american_odds`/`decimal_odds` on the chosen leg become the best available
  price for that side. `joint_prob` (ranking, floor, honesty) is untouched;
  `combined_odds` rises or holds. This is real and placeable — at one named book.

Consequence, explicitly intended (user-confirmed): the **`min_prob`-pinned axis
ranks by `combined_odds`**, so best-price legs legitimately raise those odds and
shopped cards can out-rank / re-order today's. That is the point of line
shopping — same safety, more payout, better-ranked. The `target_payout` axis
ranks by `joint_prob` (consensus) and is unaffected in ordering; its
floor is on `combined_odds`, which best price only helps clear.

## `byBookmaker` shape (confirmed against SGO docs 2026-08-06)

On each `odd`, `byBookmaker` is an object keyed by **bookmaker-id strings**
(`"draftkings"`, `"fanduel"`, …). Each entry carries:

- `odds` — American odds (string, e.g. `"-112"`)
- `overUnder` — that book's O/U line (string; present on `ou` markets)
- `spread` — that book's spread line (string; present on `sp` markets)
- `available` — boolean

(The consensus fields already read today are `bookOdds`, `bookOverUnder`,
`bookSpread` on the odd itself. Live probe was rate-limited (429, chain used the
day's free quota); shape taken from SGO docs — a single
`odds_ingest --sport mlb --dry-run` at first ingest reconfirms it, same pattern
as every prior sport's statID pinning.)

## Decisions (user-confirmed 2026-08-06)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Devig vs best price | Consensus devig for `market_prob`; best single-book price for the chosen side's payout. |
| 2 | Storage | New **nullable columns** on `prop_lines`/`game_lines`, **including the book**. |
| 3 | Ranking effect | Best price feeds `combined_odds` everywhere, incl. the `min_prob`-axis odds ranking (intended). |
| 4 | Line match | **Exact line only** — a book counts only if its `overUnder`/`spread` equals the stored consensus `line_value`. A different line is a different bet. |
| 5 | Availability | Only `available: true` book entries are eligible. |
| 6 | Best = | **Max decimal odds** (sign-agnostic best payout) among eligible books. |
| 7 | Fallback | Per-side independent: a side with no eligible book keeps the **consensus** price (today's behavior). `NULL` best columns ⇒ consensus, exactly like `model_prob=None`. |
| 8 | Scope | Sport-agnostic (all sports, wherever `byBookmaker` exists); MLB is the only live sport now. |

## Components

### 1. Ingestion — extract best price (`ingestion/odds_ingest.py`)

Pure, DB-free helper (unit-testable without SGO):

```
best_price(by_bookmaker, side_key, consensus_line) -> (american_odds:int|None, book:str|None)
```

- `by_bookmaker`: the odd's `byBookmaker` dict (per side — SGO odds are already
  one object per (market, side)).
- Eligible entry: `available is True` AND its line matches `consensus_line`
  exactly (`overUnder` for `ou`, `spread` for `sp`; moneyline `ml` has no line so
  every available entry is eligible).
- Returns the `(int(odds), book_id)` with the **maximum** `american_to_decimal(int(odds))`;
  `(None, None)` if `byBookmaker` is absent/empty or nothing is eligible.

`collect_prop_rows` attaches per (player, stat) row: `best_over_odds`,
`best_over_book`, `best_under_odds`, `best_under_book` (each side's own
`byBookmaker`, matched to that row's consensus `line_value`).

`collect_game_rows` attaches the same four for `ou` markets, plus
`best_home_odds`/`best_home_book`, `best_away_odds`/`best_away_book` for
home/away (`sp`/`ml`) markets (spread matched on `spread` == home line;
moneyline unmatched-line).

The `INSERT`s into `prop_lines`/`game_lines` gain the new columns (additive).
The consensus `over_odds`/`under_odds`/`home_odds`/`away_odds`/`line_value`
columns and their values are **unchanged**.

**`sp`/`ml` (home/away) caveat:** MLB — the only live sport — has **no** spread
or moneyline markets (its `GAME_MARKETS` are `first_inning_runs`/`f5_runs`, both
`ou`). Home/away best-price (esp. matching a book's `spread` to the stored home
line, where the away side's per-book `spread` is the negated line) is therefore
exercised only by NFL/NBA (offseason/paid-gated). Treat `sp`/`ml` best-price as
**best-effort in v1** and **reconfirm the per-book `spread`/line semantics via
`odds_ingest --sport nfl --dry-run` when those sports go live** — same
"validate on real data" discipline the project uses for every sport's statID
pinning. `ou` markets (all live MLB player props + NRFI/F5 + full-game totals)
are the fully-validated v1 path.

### 2. Schema — additive migration `db/migrations/009_line_shopping_best_price.sql`

```sql
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

All nullable, no default backfill. Existing rows stay `NULL` ⇒ consensus
fallback. **Budgerr-safe:** Budgerr has no shared DB (HTTP-only, §7.1) and
`settle` reads leg odds from the saved parlay JSONB, not these tables — so new
columns here reach no external contract.

### 3. Builder wiring (`optimizer/builder.py`, `optimizer/builder_core.py`)

- `load_player_legs` / `load_team_legs`: add the new columns to the `SELECT`
  (and the `DISTINCT ON` inner projection). NaN→None normalization in
  `_normalize` already handles absent values.
- `builder_core._base_leg`: add a `book` field (default `None`).
- `builder_core.normalize_player_leg`: after `favorite_side(over,under)` picks
  the side from **consensus** odds, choose the payout price:
  `odds = row["best_<side>_odds"] if present else row["<side>_odds"]`,
  `book = row.get("best_<side>_book")`. `market_prob` stays the consensus devig
  value. `decimal_odds` recomputed from the chosen `odds`.
- `builder_core.normalize_team_leg`: same, branching `over/under` vs
  `home/away` per `MARKET_GEOMETRY` (`best_home_*`/`best_away_*` for home/away).
- The leg dict gains `"book"`.

`build()` and the whole exact search are **untouched** — they consume
`decimal_odds`/`market_prob` as before; only the values of `decimal_odds` on
shopped legs change (upward). The exactness oracle
(`tests/test_builder_search_exactness.py`) still passes (it builds synthetic
legs directly and never touches ingestion).

### 4. Persistence + API + dashboard (additive)

- `optimizer/builder.py:save_builds`: add `"book": leg["book"]` to each
  `legs_json` entry. `construction_signature` is unchanged (keyed on
  kind/game/player/stat/market/side/line/**odds** — `odds` already captures the
  shopped price; `book` is not identity).
- `api/schemas.py:BuilderLegOut`: add `book: Optional[str] = None` (additive,
  defaulted — same pattern as the 2026-07-28 `home_team`/`away_team` add;
  Budgerr's `saved` consumption unchanged).
- `api/main.py`: populate `book=leg.get("book")` in both `/parlay-builder`
  (live search) and `/parlay-builder/saved` leg construction.
- Dashboard (`web/app/builder/…`): render the book beside each leg's price
  ("… @ -105 · DraftKings") when present; omit when `None`. Honest copy:
  "prices shopped across books — place each leg at the listed book." No
  signal-green, no EV/edge language (guardrail).

### 5. Settlement — no change

`modeling/settle.py:settle_builder_parlays` reads each leg's stored `odds` from
the JSONB for payout, so the shopped price flows through automatically. Actuals
(`player_game_stats`/`team_game_stats`) are unaffected. Zero settlement code
change.

## Guardrails (all preserved — §15.8)

1. Rank/floor on **consensus** devig `market_prob` (never `model_prob`, never a
   cross-book synthetic). ✔
2. No "+EV"/"edge"/"value"/"beat the market" language anywhere. ✔ (best price is
   framed as a better *price*, not an edge.)
3. Joint probability surfaced prominently; it is **unchanged** by shopping. ✔
4. Favorite-side legs only, `market_prob ≥ 0.55`. ✔ (floor uses consensus.)
5. Across-game only. ✔
6. Paper-only, no real-money deployment. ✔
7. Additive-only + mlb-default; Budgerr byte-unchanged on existing fields. ✔

## Testing (pure, DB-free; follow `tests/test_odds*.py` + `tests/test_builder_core.py`)

**`best_price` (ingestion):**
- picks the max-decimal price among `available` same-line books;
- skips `available: false` entries;
- skips books whose `overUnder`/`spread` ≠ consensus line (exact-line);
- moneyline: every available entry eligible (no line);
- one-sided coverage (only some books quote a side) → returns what exists;
- missing/empty `byBookmaker` → `(None, None)`;
- picks correctly across mixed signs (e.g. `+120` beats `-105`).

**`collect_prop_rows`/`collect_game_rows`:** best columns populated from a
fixture event; a line-mismatch book is excluded; consensus columns unchanged.

**Builder (`builder_core`):** chosen favorite side uses `best_<side>_odds` for
`decimal_odds` while `market_prob` stays the consensus devig value; `NULL` best
⇒ consensus fallback; `book` threads through `normalize_*` → `save_builds`
legs_json; home/away branch uses `best_home_*`/`best_away_*`.

**API:** `BuilderLegOut.book` present and defaulted; `/parlay-builder/saved`
byte-identical for existing fields (add-only). Reuse the fake-engine/queue
pattern in `tests/test_parlay_builder_api.py` (no live DB).

## Rollout / verification (architect reserved lanes)

1. `pg_dump` `prop_lines`/`game_lines` schema first (belt-and-suspenders), then
   apply migration `009` to the live DB.
2. Deploy ingestion (`ingestion/odds_ingest.py` is **not** API-imported — no
   kickstart needed for it). Run `odds_ingest --sport mlb --dry-run` to
   reconfirm `byBookmaker` field names, then a real ingest populates best_*.
3. Kickstart `com.playstat.api` (`optimizer/builder.py` **is** API-imported).
4. Verify live: a shopped MLB card's `combined_odds` ≥ its consensus-only value
   at equal `joint_prob`; each shopped leg carries a `book`; `/parlay-builder`
   and `/parlay-builder/saved` return 200; Budgerr fields unchanged; dashboard
   renders the book.
5. Old NULL rows behave exactly as today until re-ingested — no forced backfill.
6. Update README §15.9 item 3 (mark BUILT) + §15.10 in the same commit.

## Out of scope (later, separate — §15.9)

Kelly stake sizing (#4) and same-game combos (#1). Line shopping lands first and
alone.
