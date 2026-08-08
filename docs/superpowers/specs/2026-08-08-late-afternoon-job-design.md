# The Late-Afternoon Job — Design Spec

**Date:** 2026-08-08
**Status:** Design approved (user-confirmed 2026-08-08). Not built.
**Delivers:** §15.9 item 11 **Option B** (confirmed-lineup card) + the second/third
odds snapshots that make §15.9 item 12's **mandatory CLV validation gate** measurable.
**Supersedes:** the "~16:45 ET" and "79% by 19:00 ET" figures in §15.9 item 11 (both
corrected below by live measurement).

---

## 1. Why this is one piece of work

Two deliverables need the same late-day run:

1. **The confirmed-lineup card.** The morning chain builds at ~08:39 ET, but MLB
   lineups post ~2–3h before first pitch, so **16.2% of player legs still void**
   after §15.9 item 11 Option A (`--min-start-rate 0.65`). That residual is
   irreducible without real lineup data — even an everyday starter (rate ~0.85)
   sits ~15% of games, and only the posted lineup says which day that is.
2. **A second odds snapshot.** §15.9 item 12 makes CLV the **mandatory gate**
   before any "+EV" claim is trusted: the "+EV" is measured against a consensus of
   six *soft* books with no sharp reference, so best-of-six beating
   consensus-of-six may be an artifact. Today the chain takes **one** snapshot, so
   CLV is not measurable at all.

Both want a run in the late afternoon. One job serves both.

---

## 2. The blocking constraint, measured

The SGO free "Amateur" tier had been exhausted twice (08-06, 08-07), and a second
daily pull naively doubles consumption. This was the one thing that had to be
resolved before design. It was **measured, not estimated** (2026-08-08, live
against `/v2/account/usage`).

**The quota unit is `entities`, not requests:**

```
per-month:  max-entities 2500        <-- the real cap
per-minute: max-requests 10          <-- the pacing constraint (already handled)
per-day:    max-requests 500000      <-- never binding
```

**Accounting rule, directly probed:** a request with `limit=1` returning one event
incremented `current-entities` by exactly **1**. So **1 event returned = 1 entity**.

**Where today's quota goes:**

| Measurement | Value | Source |
|---|---|---|
| Today's morning pull | **51 events = 51 entities** | `logs/mlb.log` 2026-08-08 |
| Same slate, date-narrowed | **15 events**, one page, no pagination | live probe |
| Waste | **36 entities/day (70%)** on future-dated games | — |

The morning pull passes no date filter, so it returns every MLB event with odds
available — roughly 3–4 days of schedule. `load_player_legs`/`load_team_legs`
then discard all of it via `g.date = COALESCE(:slate_date, CURRENT_DATE)`. We are
paying 70% of the odds budget for rows the builder is architecturally guaranteed
to throw away.

**`startsAfter` / `startsBefore` are genuinely honored.** Verified carefully:
unknown params are silently ignored by this API (a bogus param returned 200 with
the unfiltered result set), so the filter was confirmed by *behaviour* — passing
`startsAfter=2026-08-09` returned 08-09 events instead of 08-08 events.

### The resulting budget

With `startsAfter=now` on the two later pulls (which also, and independently,
prevents in-play odds from contaminating a "closing" snapshot):

| Pull | Window filter | Entities/day |
|---|---|---|
| 08:30 | full ET slate | 13.8 |
| 17:30 | `startsAfter=now` | 9.6 |
| 19:45 | `startsAfter=now` | 3.7 |
| **Total** | | **27.1/day ≈ 840/month = 34% of the 2,500 cap** |

Against today's single unfiltered pull at 51/day ≈ **1,530/month (61%)**.

**Three pulls cost half of what one pull costs today.** The quota constraint is
not merely survivable — narrowing the existing morning pull pays for both new
pulls twice over. Decision: **narrow all three pulls** (user-confirmed).

### Adjacent win: the odds step gets dramatically faster

Today's `odds` step took **885s** (`logs/mlb.log`, 2026-08-08) — a small default
page size multiplied by the client's mandatory 6.5s inter-request pacing across
many pages. A narrowed pull with `limit=100` fits in **one request**. This is a
direct contribution to §15.9 item 7A (*the card must land pre-game*).

---

## 3. Timing, measured

§15.9 item 11's timing figures were re-derived from the live statsapi schedule
(**193 games / 14 days**) because they drive the trigger time. Two are wrong:

| §15.9 item 11 claim | Measured | Verdict |
|---|---|---|
| 23% started by 16:00 ET | 24.9% | holds |
| **79% started by 19:00 ET** | **45.1%** | **wrong** |
| **"~77% start 18:00+"** (coverage claim) | **69.4%** | overstated ~8pp |

The more useful finding: MLB's day/night split produces a **structural dead zone
from 16:10 to 18:05 ET** — across 193 games, **not one** starts in that window.
The trigger can be placed anywhere inside it at **zero coverage cost**.

**Trigger = 17:30 ET** (user-confirmed). Identical coverage to the originally
scoped 16:45 (69.4% of games), a 35-minute buffer before the 18:05 first evening
game, and 45 minutes closer to the close. It also lands more reliably *after*
lineups post: an 18:05 game posts ~15:05–16:05, an 18:40 game ~15:40–16:40.

### Why a third pull, and why 19:45

A single late pull leaves a badly skewed lead time — 17:30 is T-35min for an
18:05 game but T-4h20m for a 21:50 game. The third pull's value is **capping the
tail**, not improving the median:

| Config | Median lead | Worst-case lead | 3rd-pull cost |
|---|---|---|---|
| 08:30 + 17:30 only | 107 min | **285 min** | — |
| **+ 19:45** | 100 min | **150 min** | 3.7 ent/day |
| + 20:30 | 100 min | 165 min | 2.6 ent/day |
| + 21:00 | 96 min | 190 min | 2.4 ent/day |

**19:45** (user-confirmed) caps the worst case 40 minutes tighter than 21:00 for
1.3 extra entities/day — a rounding error against the cap.

---

## 4. Components

### 4.1 Narrow the SGO pull

**`ingestion/odds_client.py`** — `get_events()` gains `starts_after=None`,
`starts_before=None`, `limit=100`. Passed through to the `/events/` query only
when set.

**`ingestion/odds_ingest.py`** — `ingest_odds()` gains `starts_after` /
`starts_before`; `--starts-after` / `--starts-before` CLI (ISO-8601 UTC).

**Precedent to follow exactly:** §15.9 item 11 Option A's `min_start_rate` —
**library default `None` = OFF, behaviour byte-identical**, and the *chain* opts
in. This keeps the change additive and leaves every non-MLB sport untouched.

**Window computation** lives in a pure helper (`slate_window(now, sport)`), not
inline in the chain script, so it is unit-testable:

- Uses `zoneinfo("America/New_York")` — **never a hardcoded `-04:00`**. August is
  EDT; the job must still be correct in November. This is the single most likely
  source of a silent seasonal bug.
- **Exact definition** (not "roughly"): for ET calendar date `D` and
  `window_days = SLATE_WINDOW_DAYS[sport]`, the window is
  `[D 06:00 ET, D+window_days+1 06:00 ET)` converted to UTC by `zoneinfo`.
  The 06:00 ET boundary is chosen because no MLB game starts near it (earliest
  observed start is ~12:00 ET) and it sits safely clear of both the UTC date
  rollover and the 02:00 ET DST transition instant — so the window never splits a
  slate and never straddles a clock change. `startsAfter`/`startsBefore` are then
  emitted as UTC ISO-8601.
- For the 17:30 and 19:45 pulls, `starts_after` is `max(window_start, now)` — the
  window still bounds the top end, while `now` excludes already-started games.
- Must respect `builder.SLATE_WINDOW_DAYS[sport]` — NFL bets a Thu..Mon window
  (`window_days=4`) and must **not** be narrowed to one day. MLB/NBA/NHL/MLS/UCL
  are `0`. The chain is MLB-only today (`PLAYSTAT_MLB_ONLY_ODDS`), so there is no
  live NFL exposure, but the helper must be correct before that flag is ever
  flipped back.

### 4.2 Confirmed-lineup card

**New `ingestion/mlb_lineups.py`.** One call to
`statsapi.mlb.com/api/v1/schedule?hydrate=lineups` returns **both** things the job
needs:

1. the set of `player_id`s in posted lineups, and
2. each game's `gameDate` **start time**.

The second is load-bearing and easy to miss: **the `games` table has no
start-time column** (`game_id, date, home_team_id, away_team_id, status, sport`),
so "games not yet started" has *no* DB source. The lineup fetch supplies it for
free from a call already being made.

Feasibility is already de-risked (§15.9 item 11): free, no key, already-used host;
**15/15 games populate** (9 per side) and **270/270 distinct lineup player IDs map
to `players`** at 100% via the mlb `id_offset` +100M. No new provider, no
remapping, no cost.

Pure, network-free helpers — `lineup_player_ids(payload)`,
`game_start_times(payload)` — with HTTP mocked per `tests/test_ingestion_retry.py`.
**No new table**: the data is only wanted at build time.

**`optimizer/builder.py`** — `--require-confirmed-lineup`. Restricts player legs
to the posted set **and** to games not yet started. Additive param defaulting to
OFF (byte-identical when absent), mirroring `min_start_rate`. Team legs are never
filtered by lineups. Saves via `save_builds(..., "confirmed_lineup")`.

**Interaction with `--min-start-rate`:** the confirmed build should **not** also
pass 0.65. A posted lineup is direct evidence of starting; the start-rate filter
is a *proxy* for exactly that, and stacking them would drop confirmed starters
with thin history for no reason. The confirmed build passes the lineup filter
only.

### 4.3 Line movement (the CLV gate)

**New `optimizer/line_movement.py`** — deliberately **not** `modeling/clv.py`,
which was deleted in §16 #3B. This is a measurement-only rebuild, not a model
revival, and it belongs next to `optimizer/devig.py` and `optimizer/stake.py`
because it measures the builder, not a model.

**No migration required.** Both sides of the comparison already exist:

- **Build-time price** — frozen in the saved card's `legs` JSONB.
- **Later price** — `prop_lines` / `game_lines` already carry
  `pulled_at timestamptz DEFAULT now()`, and inserts are **append-only**, so each
  pull is naturally a new snapshot.

`load_player_legs` already selects
`DISTINCT ON (player_id, game_id, stat_type) ... ORDER BY pulled_at DESC`, so the
17:30 build automatically uses the freshest prices while the morning card's
recorded prices remain frozen in its own JSONB. Both behaviours are what we want
and neither needs a change.

**Definition of "closing".** For each leg, the closing reference is **the last
snapshot taken before that game's start time**. With pulls at 08:30/17:30/19:45
and `startsAfter=now` filtering, a game appears in exactly the pulls preceding its
first pitch, so its last appearance *is* its closing proxy.

**Honest naming is binding (§15.8 #2).** This is **not** the true closing line —
median lead **~100 min**, worst case **~150 min**. It must be surfaced as *"line
movement, build → last pre-start snapshot"* with the median lead time stated, and
must never be labelled "+EV", "edge", "value", or "beat the market".

**Line movement of the line itself.** `line_value` can move (e.g. 0.5 → 1.5),
in which case the later snapshot is not the same bet. **Policy: compute movement
only where `line_value` is unchanged, and report the coverage percentage
alongside.** Silently comparing across a moved line would fabricate movement that
is really a different market. Coverage is part of the output, not a footnote.

### 4.4 Dashboard + API surface

User-confirmed: surface the measurement now rather than deferring it.

- New **dashboard-only** endpoint `GET /parlay-builder/line-movement`, following
  the established `/parlay-builder/record*` pattern: backed by a **pure, DB-free
  shaping helper** (`_shape_line_movement`, mirroring `_shape_builder_record`),
  fake-engine tested.
- **Not a Budgerr surface.** `/parlay-builder/saved`, `/box-scores`, `/games`
  shapes are **untouched** (§7.1). Additive endpoint + additive schemas only.
- A builder-page section rendering movement per card, with the coverage % and the
  median-lead caption. **Read `PRODUCT.md` + `DESIGN.md` before writing any UI**
  (per `CLAUDE.md`); match `web/app/builder/` conventions; monochrome glyphs —
  **no signal-green** (reserved for the ≥75% joint-prob rule).

### 4.5 Scheduling

One script, `scripts/late_afternoon.sh`, with an `--odds-only` mode; two plists:

| Job | Time | Does |
|---|---|---|
| `com.playstat.mlb.late` | 17:30 ET | narrowed odds pull → lineups → confirmed build (1.4x + 2.0x) → save |
| `com.playstat.mlb.close` | 19:45 ET | narrowed odds pull only (closing snapshot) |

Reuse `daily_chain.sh`'s proven wrapper conventions: absolute paths (launchd's
PATH is minimal), explicit `.env` sourcing (launchd does not read it),
`_step`/`_step_retry` timing and retry, `caffeinate -w $$`, ntfy failure push.

**Failure policy:** the late job is **best-effort and must never page**. The
morning card has already landed and is the product; a missed confirmed card is a
missed *improvement*, not an outage. It writes to `logs/mlb.log` and exits
non-zero quietly.

**Idempotency:** must be safe to run twice (launchd wake/catch-up semantics —
this is the exact failure that motivated `daily_chain.sh`'s catch-up logic).
`save_builds`'s `construction_signature` dedupe already scopes to today's slate
per `(kind, class, sport)`, so a re-run does not duplicate rows.

---

## 5. Deliberate behaviour: two classes, two ledger entries

The `confirmed_lineup` card and the morning `across_game` card will **sometimes be
the same construction**. `construction_signature` dedupe is scoped per
`(kind, class, sport)`, so both are saved and the paper ledger books **two bets**.

This is the same shape as the duplicate-save bug fixed 2026-07-30 — **but here it
is intended.** Separate ledger buckets are exactly what makes the morning-vs-confirmed
comparison on void rate and ROI possible, which is the only rigorous way to prove
the void fix works. **Recorded explicitly so a future session does not "fix" it.**
Consequence: total bet count is modestly inflated; per-class records stay correct.

---

## 6. What needs no change

- **`modeling/settle.py`** — `settle_builder_parlays` dispatches per leg on
  `leg["kind"]`, not on class. `confirmed_lineup` settles automatically.
- **`GET /parlay-builder/record`** — `_shape_builder_record` groups by
  `(legs->>'class', target_payout)`, so the new bucket appears on its own.
- **Budgerr surfaces** — `/parlay-builder/saved`, `/box-scores`, `/games`
  unchanged. Budgerr fetches `?tier=all&limit=100` and partitions client-side by
  leg kind; `confirmed_lineup` rows are `kind='builder'` with the same leg shape,
  so they flow through the existing partition without a contract change.
- **No DB migration.**

---

## 7. Guardrails (§15.8, binding)

Rank only on devig `market_prob`; `market_prob ≥ 0.55`; 2–4 legs; favourite side
only; paper-only. **No +EV / edge / value / beat-the-market language** in UI, API
payloads, or recommendation JSONB — this applies with particular force to the
line-movement surface, which is the most tempting place in the codebase to imply
edge. Additive-only and mlb-default everywhere.

---

## 8. Honest expectations

- **Reported ROI should FALL.** Voids were inflating it (§15.9 item 10: cards that
  lost legs returned +11.2% vs +6.1% for intact cards). Fewer voids means fewer
  accidental vig-multiplication removals. **That is success, not regression.**
- **This does not make the builder profitable.** It remains ≈ −5.8% EV per leg
  after line shopping (§15.9 item 10). This job builds the *measurement* that
  §15.9 item 12 requires before its selection-criterion change can be trusted.
- **The gate can fail, and that is the point.** If selections do not beat the
  last pre-start line, the "+EV" of item 12 is noise and no amount of filtering
  will make it profitable. The job must be able to return that answer.
- **Coverage ceiling: 69.4% of games.** The ~30% starting before 18:05 ET are
  inherently out of scope for a 17:30 confirmed card.

---

## 9. Testing

No test DB — `ingestion.db.get_engine()` is **LIVE**. Every new test is pure or
uses the established fakes (`tests/test_builder.py:_CapturingEngine`,
`tests/test_parlay_builder_api.py:_fake_engine`, `tests/test_ingestion_retry.py`
for HTTP).

- `slate_window()` — pure; **DST both sides** (August EDT and November EST), and
  `SLATE_WINDOW_DAYS` respected for NFL.
- `lineup_player_ids` / `game_start_times` — pure, against a captured payload.
- `--require-confirmed-lineup` — filters to the posted set; excludes started
  games; **absent ⇒ byte-identical** to today.
- Line movement — unchanged-line comparison, coverage %, and the moved-line case
  **excluded** rather than silently compared.
- `_shape_line_movement` — pure/fake-engine.
- Regression: `/parlay-builder/saved` byte-unchanged.

Full `pytest` (374 green today) run by the architect, plus `tsc` + `next build`.

---

## 10. Reserved lanes (architect only)

launchd job creation, `git push`, live DB writes, browser verification, and
`launchctl kickstart -k gui/$(id -u)/com.playstat.api` after any change to an
API-imported module (`optimizer/builder.py` is API-imported; `ingestion/*` and
`scripts/*.sh` are not).

## 11. Escalate

Anything consumer-breaking for Budgerr, paid API plans, deployment, or
deleting/overwriting anything not created by this work.
