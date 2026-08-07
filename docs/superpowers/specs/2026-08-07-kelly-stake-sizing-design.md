# Kelly stake sizing — Design Spec

**Date:** 2026-08-07 · **Roadmap:** README §15.9 item 4 · **Status:** design (approved in brainstorm)

## Problem

The paper ledger stakes a flat **1u** on every builder parlay (`recommendation_outcomes.stake`
hardcoded to `1` in `modeling/settle.py:settle_builder_parlays`). Item 4 replaces that with a
**¼-Kelly** stake per parlay-as-one-bet, plus a **same-night total-exposure cap**. Additive,
paper-only, no change to settle's payout math or any Budgerr contract shape.

## The core judgement — where the edge comes from (user-confirmed 2026-08-07)

The builder ranks on the **consensus de-vigged `joint_prob`**, which assumes ≈no edge. Full Kelly
on a fair-priced bet says stake ~0 — correct: paying vig on a fair-prob bet is −EV. The *only*
genuine edge in this system is **line shopping**: the best single-book price (`combined_odds`,
already shopped per-leg in item 3) can exceed the consensus fair price `1/joint_prob`.

So the per-parlay edge is defined as:

- **p** = `parlay_recommendations.joint_prob` (consensus de-vig fair joint probability — unchanged,
  still the ranking quantity; guardrail §15.8 #1 preserved).
- **d** = `parlay_recommendations.combined_odds` (the shopped best-price decimal payout the paper
  bet actually receives).
- **Kelly fraction:** `f* = (p·d − 1) / (d − 1)`, **clamped to ≥ 0**.

The edge `p·d − 1` is the true expected value of the bet *at the price it is actually booked* (`d`).
It is meaningfully positive mainly where line shopping lifts `d` above the consensus fair price. It is
**~0 on most consensus-priced cards** but — validated on the live 2026-08-07 slate — **not exactly 0**:
`market_prob` (the two-sided devig consensus) and the stored consensus price don't perfectly agree, so
`p·d − 1` can be slightly positive or negative even with no shopping (e.g. a −500 leg whose devig
`market_prob` is 0.8603 > the 0.833 that price raw-implies). Kelly correctly sizes that small residual
tiny, and clamps to 0 (stake 0) whenever `p·d ≤ 1` — which on the live slate zeroed **5 of 8** cards,
several at a *negative* edge (heavy-favorite parlays that look "safe" at ~68% but are badly priced even
after shopping). So the behaviour is "stake proportional to fair-prob × booked-price − 1," NOT "stake
only where a book strictly beat consensus." Kelly actively distinguishing well-priced from badly-priced
cards is the intended value; the narrower "shopping-gain-only" variant (f*=0 unless a book beat
consensus) is a deliberately-rejected alternative (it would need the consensus combined odds stored
separately and would ignore real EV from the devig-vs-price gap). (Consequence: while best-prices were
NULL — SGO quota out 08-06/08-07 — every stake was 0; with a fresh key 08-07, the live slate stakes
3/8 cards at ~0.35–0.43u, total 1.15u.)

## Unit convention (user-confirmed)

Kelly's `f*` is a fraction of bankroll. Convention: **1 unit = 1% of bankroll** (bankroll = 100u).

```
stake_units = fraction · f* · bankroll_units      # fraction = 0.25 (¼-Kelly), bankroll_units = 100
            = 0.25 · f* · 100  =  25 · f*
```

Worked (¼-Kelly): edge `p·d−1 = 2%` on a 2.0x parlay → `f*≈0.02` → **~0.5u**; `4%` → **~1.0u**
(same scale as today's flat bet); `8%` on a short parlay → **~2u**. This keeps ledger P&L legible
against the existing 1u-scale history (ROI is already staked-denominated — see below). All three
constants are parameters (`--kelly-fraction 0.25`, `--bankroll-units 100`) with these defaults.

## Same-night total-exposure cap (user-confirmed: 5u global default, scope configurable)

The summed paper stakes over a night are capped; if the sum exceeds the cap, every stake in the
group scales down proportionally (`stake_i *= cap / Σstake`).

- **Default cap:** `5.0` units (5% of bankroll/night) — `--exposure-cap 5.0`.
- **Default scope:** **global per-date** (all sports on date D share one 5u budget) — `--cap-scope global`.
- **Configurable scope:** `--cap-scope per-sport` gives each sport its own cap per date. Sport is
  read from the legs JSONB (`legs->>'sport'`, default `mlb` when absent).

¼-Kelly is already conservative, so the cap mainly catches fat-slate pile-ups; it's a backstop, not
the usual binding constraint.

## Architecture

Five units, each independently testable. **Additive only.**

### 1. Pure math — `optimizer/stake.py` (DB-free)

```
kelly_fraction(p, decimal_odds) -> float            # (p·d − 1)/(d − 1), clamped ≥ 0; 0 if d ≤ 1
quarter_kelly_stake(p, decimal_odds, *, fraction=0.25, bankroll_units=100) -> float
apply_exposure_cap(stakes: list[float], cap: float) -> list[float]   # proportional scale-down if Σ>cap
```

All pure, no imports beyond stdlib. This is the entire risk surface for the math; unit-tested
exhaustively (no DB).

### 2. The stake-sizing pass — `optimizer/stake.py:main` (new chain step)

The builder writes cards in **separate per-(sport,target) processes**, so no single builder run
sees the whole night. One pass runs **once after all builder steps, before `settle`**, over a
date's `kind='builder'` rows:

1. Load date D's builder recommendations: `parlay_id, joint_prob, combined_odds, legs->>'sport'`.
   Date = `(created_at AT TIME ZONE 'America/New_York')::date` to match §15.10's ET slate reasoning.
   `--date` defaults to today (ET).
2. Compute `quarter_kelly_stake` per parlay.
3. Group by cap scope (`global` → date; `per-sport` → (date, sport)); `apply_exposure_cap` per group.
4. `UPDATE parlay_recommendations SET stake = :s WHERE parlay_id = :pid`.

**Idempotent:** recompute-from-scratch each run (deterministic from joint_prob+combined_odds); safe
to re-run (e.g. after a manual re-build). CLI: `python -m optimizer.stake [--date …] [--exposure-cap 5.0]
[--cap-scope global] [--kelly-fraction 0.25] [--bankroll-units 100]`.

### 3. Storage — migration `010` (architect reserved lane; back up schema first)

Additive **nullable** `stake NUMERIC` on `parlay_recommendations`. `NULL` = "not sized" (all
historical rows). Budgerr's `/parlay-builder/saved` does not select it → byte-unchanged.

### 4. Settle integration — `modeling/settle.py:settle_builder_parlays`

Read `pr.stake` in the candidate query. Pass it to `parlay_result(results, odds, stake=s)` and use
it as the INSERT's `stake` value. **`pr.stake IS NULL → fall back to 1.0`** (current behaviour
preserved — non-breaking for any unsized row). `parlay_result`/`single_pnl` already accept `stake=`;
**payout math is untouched** (pnl scales linearly with stake, which it already did at stake=1).

### 5. Ledger/record ROI — must denominate by Σstake

Variable stakes only aggregate correctly if ROI = `pnl / Σstake`, not `pnl / n`.

- **Already correct** (no change): `aggregate_bet_performance` + `/bet-performance` (`api/main.py:610`)
  + the all-time summary (`settle.py:436`) — all use `pnl/staked`, and `staked = SUM(ro.stake)` is
  already a first-class GROUP BY quantity.
- **Fix** (dashboard-only, additive): `_shape_builder_record` (`api/main.py:497`) and
  `_shape_builder_record_daily` (`:545`) currently compute `pnl/n`. Add `SUM(ro.stake)` to their
  queries and change ROI to `pnl/staked` (0.0 when staked==0). These back `GET /parlay-builder/record`
  + `/record/daily` — dashboard-only, NOT Budgerr surfaces (§15.10), so safe.

## Decisions (user-confirmed)

- **(a) stake=0 cards are recorded normally** — they settle, add to W-L counts, contribute 0 to both
  `pnl` and `Σstake` (so ROI is unaffected by them). Keeps the full paper trail. `parlay_result` with
  stake=0 returns pnl=0 for win/loss/push alike — verify no divide-by-zero anywhere (ROI already
  guards `staked else 0.0`).
- **(b) stake is surfaced only in the settled record, NOT on the live card.** `stake` stays out of
  `/parlay-builder/saved` (Budgerr-adjacent). The dashboard record region shows staked units +
  (now-correct) stake-weighted ROI. Showing the *suggested* stake on tonight's un-settled card is a
  clean follow-up, deliberately deferred.

## Honest framing (guardrail §15.8 #2 — binding)

UI/API/JSONB copy frames this strictly as **stake sizing**. NO "+EV" / "edge" / "value" / "beat the
market" language, no signal-green. Acceptable phrasing: "¼-Kelly stake", "sized on shopped price",
"sized to 0 (no shopped price advantage)". The record caption keeps the existing small-sample /
paper-only honesty notes.

## Testing (no test DB — pure + fake-engine per tests/test_builder.py)

- **Pure** (`tests/test_stake.py`): `kelly_fraction` (fair price → 0; shopped uplift → positive;
  d≤1 → 0; clamp on negative); `quarter_kelly_stake` unit scaling (2%/4%/8% → ~0.5/1.0/2.0u);
  `apply_exposure_cap` (under cap → unchanged; over cap → proportional, Σ==cap; all-zero → unchanged).
- **Fake-engine** (`tests/test_stake_pass.py`): the pass reads rows, computes, UPDATEs the right
  stakes, respects scope grouping, is idempotent on re-run (`_CapturingEngine` / queue pattern).
- **Settle** (`tests/test_settle_builder.py` additions): a sized parlay (stake=0.7) books pnl =
  0.7·(d−1) on win, −0.7 on loss; a NULL-stake parlay still books at stake 1.0 (fallback);
  a stake=0 parlay books pnl 0 and appears with 0 staked.
- **Record shapers** (`tests/test_parlay_builder_api.py` additions): ROI = pnl/Σstake with mixed
  stakes (not pnl/n).

## Out of scope (explicit)

- Same-game correlation / copulas — that's §15.9 item 1 (next).
- Live-card suggested-stake display — deferred follow-up (decision b).
- Any change to `combined_odds` / `joint_prob` computation or the builder search.

## Dependency / current state

**RESOLVED 2026-08-07:** a fresh free SGO account key is in `.env`; best-prices now populate
(1984/2355 prop_lines, 28/33 game_lines shopped, real books). Today's rebuilt slate was measured
against the exact formula above: **3/8 cards stake (~0.35–0.43u), 5/8 stake 0, total 1.15u** — a
concrete, non-degenerate distribution proving Kelly does real work on live prices. The MLB-only +
resilient chain (commit `6e2eabe`) makes the fresh 2,500-object budget last. Re-validate magnitudes
after the feature lands + the next chain runs the stake pass (per the "validate perf on real data"
discipline).
