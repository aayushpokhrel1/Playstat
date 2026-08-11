# Sharp-reference snapshot (Pinnacle via The Odds API) — Design Spec

**Date:** 2026-08-11 · **Status:** approved (user: "build it here"), built same session
**Context:** README §15.9 item 14 — the kill test everything else waits on.

## Problem

Every profitability question in the repo now funnels into one unknown: is there a
trustworthy fair price? Item 14 measured (a) shopped outliers LEAD the market
(anchorless shopping anti-selects), (b) no exploitable drift, (c) realized leg
returns +7.6% that contradict the soft-close CLV gate. Only an external sharp
price adjudicates (c) and turns (a) from a trap into a signal.

**Verified live 2026-08-11 (5 credits):** The Odds API `eu` region carries
Pinnacle for all 15 MLB events — `h2h` + `totals` at ≈2.9% overround — and
per-event `totals_1st_1_innings` (NRFI), `totals_1st_5_innings` (F5), and
`batter_home_runs`. Credit meter: mainlines 2/slate-pull; event markets
1/market/event. Free tier 500/mo ⇒ one close-time, card-games-only snapshot/day
(~35 credits) fits a 14-day trial; full coverage needs the ~$30/mo tier.

## Design (v1 = the kill test, nothing more)

1. **`ingestion/theodds_client.py`** — `TheOddsClient`, mirroring
   `odds_client.py`'s retry shape: `(ConnectionError, Timeout)`/429/5xx retried
   with backoff, 401/403 fail fast, `timeout=(10, 30)`, 1s pacing. Captures the
   `x-requests-*` headers (the credit budget) as `last_headers`. Config:
   `THEODDS_API_KEY` (env `THE_ODDS_API_KEY` — NOT SGO's `ODDS_API_KEY`).
2. **`db/migrations/011_sharp_lines.sql`** — append-only `sharp_lines`:
   `(id, game_id, player_id NULL, market, line_value NULL, book,
   over_odds/under_odds/home_odds/away_odds NULL, pulled_at)` — the same
   side-pair column convention as `game_lines`, our market vocabulary
   (`home_runs`, `first_inning_runs`, `f5_runs`, `total_runs`, `moneyline`).
   No FK constraints (matches `prop_lines`/`game_lines` precedent); additive,
   nothing reads it yet.
3. **`ingestion/sharp_ingest.py`** — the snapshot CLI:
   - Loads today's saved builder-card legs → the set of (game, market[, player])
     actually bet. **Card-games-only is the budget rule.**
   - Maps our `game_id`s to The Odds API event ids via the **free** `/events`
     endpoint: exact team-name match (`teams.name` ↔ `home_team`/`away_team`)
     on the same ET date. Unmatched games are logged and skipped, never guessed.
   - One slate-wide `h2h,totals` pull (2 credits — context + ML/totals anchor),
     then per-event pulls restricted to the card's markets via a fixed
     `MARKET_MAP` (ours → theirs). Requesting an unoffered market bills 0, so
     the map includes SB/RBI/hits/runs/walks/TB/K prop keys; coverage decides.
   - Player props matched by accent/case-normalized name
     (`unicodedata`-strip ↔ `players.name`); unmatched → logged, skipped,
     counted. Books kept: `pinnacle` (v1; exchanges deliberately excluded —
     one reference, no ambiguity about which number is the anchor).
   - `--dry-run` (no insert), `--budget-guard N` (default 60: skip the run if
     `x-requests-remaining` < N so month-end never zeroes the meter mid-trial).
4. **`optimizer/sharp_compare.py`** — pure, beside `devig.py`/`line_movement.py`:
   de-vig the Pinnacle two-sided pair for OUR side at the EXACT line (a moved
   line is a different bet → excluded, counted in coverage, same rule as
   `line_movement.py`); emit per-leg `{fair_prob, booked_decimal, fair_ratio}`
   where `fair_ratio = fair_prob × booked_decimal` (>1 ⇔ booked price beats
   sharp fair). **CLI report only** — no API endpoint, no dashboard, no stored
   JSON, so §15.8 #2 is satisfied by construction, not by careful wording.
   `python -m optimizer.sharp_compare --days N` prints the trial verdict table.
5. **Chain wiring** — one non-fatal, key-gated step appended to
   `late_afternoon.sh`'s `--odds-only` (19:45 close) branch, inside the existing
   freshness window + `PLAYSTAT_LATE_CMD` smoke hook. Off when
   `THE_ODDS_API_KEY` is unset.

## Decisions

- **Close-only snapshots.** The booked build price is already stored on the
  card; the comparison needs only the sharp close. Morning sharp pulls would
  double the budget for context, not verdict.
- **Pinnacle only in v1.** Betfair/Matchbook ride the same pulls for free but a
  single named anchor keeps the verdict unambiguous; exchanges can be added to
  `BOOKS` later without schema change.
- **No new API/UI surface.** The verdict is for the operator, not the product.
  Budgerr byte-unchanged; dashboard untouched.
- **Exact-line matching, one-sided rows excluded** — inherited honesty rules.

## Kill test (2–3 weeks)

(i) How often does a booked best price exceed devigged Pinnacle fair
(`fair_ratio > 1`), and by how much? (ii) Do selections beat the Pinnacle close
(sign of `fair_prob − build market_prob`)? If (i) ≈ 0 or (ii) negative, item
12's honest ceiling stands and the fall path is item 2's data-research pass.
