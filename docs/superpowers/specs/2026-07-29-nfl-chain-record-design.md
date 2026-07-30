# NFL backend chain + record sport-filter (#4a) — design spec

Sub-project **#4a of the NFL parlay-builder decomposition** (the backend half of
the original #4; user-confirmed 2026-07-29 to split #4 into **#4a backend** — this
doc — and **#4b dashboard NFL view**). Build order: #1 odds ingestion (BUILT) → #2
player-prop tier (BUILT) → #3 game-markets tier + settlement (BUILT) → **#4a
backend chain + record (this doc)** → #4b dashboard.

Goal: make NFL builder parlays **build, settle, and report on their own weekly
cadence, end-to-end, inside the existing daily chain** — with no dashboard changes
(that is #4b). NFL is offseason, so this is built now and verified structurally +
by `--dry-run`/the first live Thursday build at preseason (~August).

## Decisions (user-confirmed 2026-07-29)

- **Split:** #4a is backend only. The dashboard NFL view is #4b (its own cycle).
- **Weekly slate window (Thu–Mon)** for NFL, not MLB's single-day "today". A
  parlay spanning the week's games (TNF + Sunday + MNF) is normal and placeable
  for NFL, and single-game Thu/Mon days can't form an across-game parlay alone.
- **Fold into the existing daily chain**, no new launchd job.
- **Build once per week (Thursday-gated).** The consequence the weekly window
  forces: a rolling window rebuilt *every day* would save Sunday's slate in ~6
  daily builds → ~6× duplication in the paper ledger when it settles (inflated n,
  distorted ROI). MLB avoids this because its "today" window puts each game in
  exactly one build. Gating the NFL *build* to Thursday saves each weekly slate
  once; settlement + score ingestion still run daily so each game settles as it
  finishes.

## Key findings (from the code, 2026-07-29)

1. **Builder slate filter is single-day.** `load_player_legs`/`load_team_legs`
   (`optimizer/builder.py`) both filter `JOIN games g ... AND g.date =
   COALESCE(:slate_date, CURRENT_DATE) AND g.sport = :sport`. Two tests assert
   this **literal string**: `test_games_join_has_date_predicate_defaulting_to_current_date`
   and `test_loaders_filter_games_join_by_sport` (`tests/test_builder.py`) — both
   must be updated to the range form.
2. **Chain is MLB-shaped but sport-parameterized underneath.** `scripts/daily_chain.sh`
   runs the MLB builder steps daily. `odds_ingest --sport`, `builder --sport`,
   `nfl_backfill --only games`, and `modeling.settle` (with the #3 NFL branch) all
   already exist and are sport-aware; #4a only adds chain *wiring*, no new module.
3. **`settle` already handles NFL (#3)** — total/spread/moneyline vs final scores,
   reading `team_game_stats('points')`. It needs those `points` rows fresh, which
   `nfl_backfill --only games` writes (BUILT #3, backfilled live).
4. **Record endpoints have no sport filter.** `builder_record()` +
   `builder_record_daily()` (`api/main.py`) both `WHERE pr.kind='builder'` GROUP
   BY class/target (or date), no sport. They are **dashboard-only** (added
   2026-07-28, `/bet-performance` untouched — NOT a Budgerr surface).

## Components

### A. Per-sport builder slate window — `optimizer/builder.py`

Generalize the single-day filter to an inclusive date **range**:

```
AND g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)
              AND COALESCE(:slate_date, CURRENT_DATE) + :window_days
```

(Postgres `date + integer` adds days; `+ 0` ⇒ the same single day.)

- Add a `window_days: int = 0` param to `load_player_legs`, `load_team_legs`,
  `load_legs`, threaded into the query params. **`window_days=0` is semantically
  identical to today's `g.date = ...`** (`BETWEEN base AND base` ⇔ `= base`), so
  MLB is unchanged in behavior; only the SQL text changes.
- Per-sport default map in `optimizer/builder.py`:
  ```python
  SLATE_WINDOW_DAYS = {"mlb": 0, "nfl": 4}   # nfl: Thu..Mon inclusive
  ```
  `main()` resolves `window_days = args.window_days if args.window_days is not None
  else SLATE_WINDOW_DAYS.get(args.sport, 0)` and passes it to the loaders.
- CLI: add `--window-days` (int, default `None` ⇒ use the per-sport default).
  The daily chain does not need to pass it (the `--sport nfl` default is 4).
- **Update the two literal-string tests** to assert the new range predicate
  (`g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)` …). Keep the `g.status !=
  'FT'` and `g.sport = :sport` assertions.
- The `slate_date` semantics are unchanged (the window's lower bound; default
  today, server-tz). `dedupe_by_price` / the across-game search are untouched —
  a wider candidate pool is still just legs across distinct games.

**Why 4 and not 6/7:** from a Thursday build, `today..today+4` = Thu, Fri, Sat,
Sun, Mon — exactly one NFL week (TNF → MNF, incl. rare Fri/Sat games). A larger
window would start pulling the *following* week's games (whose lines may already be
posted) into the same card. 4 is the tight, correct span for the Thursday cadence.

### B. Daily-chain NFL wiring — `scripts/daily_chain.sh` (architect's reserved lane)

Two additions, both inside `run_chain`, using the existing `_step`/`_step_retry`
helpers and preserving the `&&` short-circuit:

1. **Thursday-gated NFL build block** (`[ "$(date +%u)" -eq 4 ]` — Thu). Runs
   after the MLB builder steps, before/independent of settle:
   - `_step_retry nfl_odds "$PY" -m ingestion.odds_ingest --sport nfl`
   - `_step nfl_builder_1.4 "$PY" -m optimizer.builder --sport nfl --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save`
   - `_step nfl_builder_2.0 … --target-payout 2.0 …`
   - `_step nfl_game_1.4  … --sport nfl --team-only --target-payout 1.4 … --max-leg-reuse 2 --save`
   - `_step nfl_game_2.0  … --team-only --target-payout 2.0 …`
   The builder self-reports "no candidate legs" and exits 0 when the slate is empty
   (offseason, or a Thursday with no lines yet) — expected, not a failure (same as
   the MLB `--team-only` tier).
2. **Daily NFL score ingestion** — `_step_retry nfl_scores "$PY" -m
   ingestion.nfl_backfill --only games` — runs **every day**, before `settle`, so
   finished NFL games get `status='FT'` + their `points` rows and the Thursday-built
   parlays settle progressively across the week (TNF→Fri, Sunday→Mon, MNF→Tue).
   `--only games` skips the heavy player-stats backfill (BUILT #3).

Structure: gate the build block with a shell `if`; `nfl_scores` is ungated. Both
no-op cheaply out of season (empty slate; the schedule CSV pull is small). Keep the
network steps (`nfl_odds`, `nfl_scores`) on `_step_retry` (one delayed re-run) like
the other ingestion steps; the pure builder/settle stay on `_step`.

> Cadence note (documented in the script): NFL **builds** Thursdays only (once per
> weekly slate, to keep the ledger from double-counting); NFL **settles** daily as
> games finish. Offseason: both no-op.

### C. Record endpoints sport-filter — `api/main.py`

Add an additive `sport: str = "mlb"` query param to `builder_record()` and
`builder_record_daily()`, and to each SQL:

```
WHERE pr.kind = 'builder'
  AND COALESCE(pr.legs->>'sport', 'mlb') = :sport
```

- Mirrors `/parlay-builder/saved`'s COALESCE default exactly. The existing
  dashboard call (no `sport`) resolves to `mlb` and returns MLB rows only —
  **excluding** any NFL rows from the MLB record (the point: NFL and MLB records
  must not pool). No NFL rows exist yet, so no behavior change today.
- The pure `_shape_builder_record` / `_shape_builder_record_daily` are unchanged
  (they already handle an unknown class via `_CLASS_TO_TIER.get(cls, cls)`; NFL's
  `game_tier`/`across_game` shape fine).
- #4b's dashboard passes `?sport=nfl` to get the NFL record.

## Out of scope

- **Dashboard NFL view (#4b):** the sport toggle/section, NFL tiers rendering,
  and wiring `?sport=nfl` through `web/app/builder/` + `web/app/lib/api.ts`.
- **NFL CLV** (multiple odds snapshots/week) — §15.9 future; the Thursday single
  pull is enough to build.
- **NFL-calendar-aware week boundaries** — the fixed `today..today+4` Thursday
  window is the YAGNI-correct span; a true week-number model isn't needed.
- Any prediction-model work (shelved, §16).

## Verification (offseason — no NFL odds yet)

CRITICAL SAFETY: NO test DB; `ingestion.db.get_engine()` is LIVE. All tests pure
or `_FakeEngine` (`tests/test_parlay_builder_api.py` / `test_builder_record_api.py`
patterns — the latter's fake-engine, and monkeypatch `main.engine`). Worktree:
`graphify-out/` absent → read source directly; interpreter
`/Users/aayushpokhrel/dev/playstat/.venv/bin/python`.

- **Builder window (pure/source-inspection + DataFrame):**
  - the two updated literal-string tests assert the new `BETWEEN` predicate (+ the
    retained `g.status != 'FT'` / `g.sport = :sport`);
  - `SLATE_WINDOW_DAYS` map + `main()` resolves nfl→4, mlb→0, `--window-days`
    overrides;
  - `load_*` thread `window_days` into the query params (source/inspection, since
    the SQL execution needs the live DB — do NOT hit it).
- **Record sport-filter (fake-engine):** `builder_record(sport='nfl')` /
  `builder_record_daily(sport='nfl')` add the COALESCE filter and thread `:sport`;
  default `mlb`. Follow `tests/test_builder_record_api.py`'s existing fake-engine
  harness.
- **Chain (architect):** `bash -n`; verify the Thursday gate (`date +%u`) with a
  smoke run forcing a Thursday vs non-Thursday (e.g. via `PLAYSTAT_CHAIN_CMD` or a
  wrapped `date`), confirming the NFL build block runs only on Thu and `nfl_scores`
  runs daily. Confirm MLB steps unchanged.
- **Live (architect, at preseason):** `odds_ingest --sport nfl --dry-run` for
  coverage; the first real Thursday build produces + saves an NFL card; scores land
  and it settles across the week; `/parlay-builder/record?sport=nfl` reports it.
- **Read-only now (architect):** `load_legs(sport='nfl', window_days=4)` against
  the live DB returns [] (no NFL odds) without error; MLB `load_legs()` unchanged
  (byte-identical result count vs pre-change).
- Full suite stays green (currently 332).

## Done criteria

1. Builder has a per-sport slate window (`SLATE_WINDOW_DAYS`, `--window-days`);
   `window_days=0` keeps MLB byte-identical (behavior); NFL uses Thu–Mon (4).
2. Daily chain builds NFL Thursdays (odds + player ×2 + game-tier ×2, saved) and
   ingests NFL scores + settles daily; offseason/non-Thu no-op; MLB steps unchanged;
   `bash -n` clean.
3. `/parlay-builder/record` + `/record/daily` accept `?sport` (default mlb,
   additive); NFL and MLB records don't pool.
4. New/updated tests green (pure/fake-engine); full suite green.

## Split of labor

- **Worktree agent:** Component A (builder window + the 2 test updates) and
  Component C (record sport-filter + tests). Pure/fake-engine only; never the live
  DB; does NOT touch `scripts/daily_chain.sh`; does not push.
- **Architect (reserved lanes):** Component B (`daily_chain.sh`), the API kickstart
  after the `api/main.py` change, the live read-only + preseason verification, and
  the merge.
