# Findings

What has been measured here, and what each measurement rules out. This exists so
nobody re-derives it, and so no ROI number in this repo gets read without its
context.

Each finding is dated because it is a measurement at a sample size, not a
permanent truth. Where a research spec exists it is linked and holds the detail.

**Read finding 1 before reading any ROI number anywhere in this repo.**

---

## 1. The vig arithmetic - why the builder is structurally negative-EV

**Researched 2026-08-08.**

The builder ranks on de-vigged market probability and bets at the offered price.
If the market is efficient and the de-vig is right, expected ROI is
`(1/overround)^n_legs − 1`. That is not a risk; it is the arithmetic of the
product.

Measured on 6,050 live-priced legs:

- Mean EV per leg at consensus prices: **−7.14%**.
- With line shopping (84.1% of legs got a shopped best price): **−5.80%**, a
  **+1.34pp** recovery - about **19% of the vig**.
- Legs reaching a positive shopped EV: **0.8%**.

So line shopping helps and nowhere near enough. **Ranking by safety among
negative-EV legs guarantees a negative-EV product**, and parlaying multiplies the
drag rather than diluting it.

**What could create edge** (none currently in place): more books or wider shopping
coverage; a genuine mispricing signal (ruled out - finding 5); or exploiting book
SGP correlation mispricing (rejected - books reprice correlated legs).

## 2. The hit-rate illusion - a 75-23 record is not a winning system

**Researched 2026-08-11.** Recorded so the arithmetic is not re-derived hopefully.

The natural reading of the ledger is "the 1.4x tier is 75-23, that must be printing
money." It is not.

- **Flat-staked, the whole 1.4x record makes +$16.60.** At a flat $10/card over the
  real settled odds: **75W-23L-2P, +$16.60 on $1,000 staked = +1.7% ROI.** The live
  Kelly-staked figure is +0.69u on 80.37u = **+0.9%**; flat looks better only
  because Kelly correctly stakes less on the thinner cards.
- **The price already demands ~74%.** At 1.4x a win pays $4 and a loss costs $10,
  so ~2.5 wins are needed per loss. On the settled odds (avg 1.347x): break-even
  hit rate **74.2%**, actual **76.5%**, margin **+2.3pp**, 95% CI
  **[68.1%, 84.9%]** - which **contains** break-even.
- **A high hit rate is free, and it is not the goal.** Measured live by asking the
  builder for it: **≥65%** → pays 1.50x, hits 65.1%, needs 66.7% (short 1.6pp);
  **≥75%** → 1.24x, 75.2%, needs 80.6% (short 5.4pp); **≥80%** → 1.17x, 80.3%,
  short more still.
- **The direction is the punchline: chasing safety makes it WORSE**, monotonically,
  from −1.6pp at 65% to −6.8pp at 90%. Every added leg or heavier favourite
  multiplies the ~7% vig while the payout collapses toward 1.0.

**Hit rate and payout are welded together by the price.** You can slide anywhere
along that line and every point on it is slightly negative. The only way off the
line is a genuine mispricing.

## 3. Voids inflate the record, and fixing them makes the number worse

**Found 2026-08-08.**

**18.3% of all legs void** (21% of player legs; team legs 0%) - the game is final
but no stat row exists, because the player never appeared. By market: `runs`
**26.1%**, `home_runs` **22.3%**, `stolen_bases` **14.3%**. **34.9% of cards lose
≥1 leg.** Root cause is **timing, not settlement**: the chain built at ~08:39 ET
while MLB lineups post 2-3h before first pitch.

A void removes a leg, which removes one ~7% vig multiplication, turning a 4-leg
card into a shorter, better-EV bet. Measured: cards that lost legs returned
**+11.2%** (n=67) against **+6.1%** for intact cards.

**Therefore fixing voids must make measured ROI fall, and that is correct.** Both
fixes shipped 2026-08-08: a start-probability filter at build time (Option A) and a
second confirmed-lineup pass at 17:30 (Option B).

**An honest correction on Option A's benefit**, kept because the first number is
the kind that gets quoted: the initial estimate (voids 18.4% → 7.6%) came from a
crude calendar-day proxy on de-duplicated pairs. Replayed with the **real,
team-normalised** filter over all 415 historical player legs, the benefit is
**smaller** than originally scoped. `load_start_rates` normalises appearances by
**their team's** finished games over the prior 21 days - a calendar-day denominator
understates everyone.

## 4. The ledger validates correctness, not profitability

**Researched 2026-08-08**, on 192 settled MLB builder parlays / 475 legs,
2026-07-21 → 08-07.

- **(a) The across-game independence assumption is VALIDATED** - the most important
  result, because the whole joint-probability claim rests on it. Raw parlay hit
  rates sit *above* the market product (1.4x +5.9pp, 2.0x +11.3pp, team +21.5pp),
  which looks like hidden positive correlation. It is not: once each leg is
  credited with its own empirical hit rate, the gap closes.
- **(b) Player-prop legs are CALIBRATED.** n=328 decided, predicted **0.823** vs
  actual **0.841** (+1.85pp, inside the ±3.95pp CI). The de-vigged market price is
  honest - so **there is no free accuracy to reclaim** on player legs.
- **(c) The apparent team-market edge is NOISE.** Team legs read **+15.2pp** over
  their de-vigged price (n=60) and are the source of the team tier's flattering
  ROI. But those 60 legs are only **33 distinct games across 10 slate days**;
  de-duplicated to independent events, it collapses. **The same leg rides multiple
  cards** - this is the trap to check first in any leg-level statistic here.
- **(d) Heavy concentration in two markets.** `home_runs` (175) + `stolen_bases`
  (133) = **65%** of all legs, overwhelmingly **unders (300) vs overs (28)** - a
  structural consequence of taking the de-vigged favourite on rare-event props
  ("won't homer", "won't steal").
- **(e) THE RECORD PREDATES THE CURRENT SYSTEM.** Line shopping produced a shopped
  price on **0%** of legs through 2026-08-06, then 65.8% (08-07) and 72.7% (08-08);
  Kelly stakes likewise exist only from 08-07. **All 192 settled parlays measure a
  system that no longer exists.**
- **(f) Sample-size reality.** At ~11 cards/night, detecting a genuine few-point
  edge against a sharp market needs **thousands** of settled parlays - months. No
  ROI figure in the ledger, including the team tier's, should be read as a verdict.

## 5. Predictive signal: NO-GO

**Researched 2026-08-13, closed 2026-08-14.**
[Full findings](superpowers/specs/2026-08-13-predictive-signal-sourcing-research.md).

Six candidate signal families tested against a bar set **before** the data. The
decision and its consequences are in [PRODUCT.md](../PRODUCT.md#rebuilding-the-model-no-go);
the results worth keeping here:

- **Sourcing came back entirely positive.** Five families live in one free,
  key-less endpoint already called (`statsapi …/feed/live`) at **13.5 KB/game**
  with `?fields=` pruning - 60× smaller than the raw feed. The full 3-season spine
  is 6,682 games, ~87 MB, ~4 minutes. Cost was never the constraint.
- **Baseline OOS R² 0.0051 / 0.0110 / 0.0108** (NRFI / F5 / full-game) -
  an independent replication of the earlier F5 R²=0.007.
- **Only two families cleared the bar**, both on run environment (lineups +2.19pp
  full-game).
- **Stage 3, the decisive test - the market encompasses our forecasts** on every
  market actually bet. The market basis was deliberately flexible (line, p, p², p³,
  logit p, −ln(1−p)) so no result could come from functional-form arbitrage.
- **The umpire effect does not exist - absent, not weak.** Over 6,682 games and 84
  umpires with ≥40 games, observed sd of per-umpire mean strikeouts **0.476** vs
  **0.480** expected from sampling noise *if all umpires were identical*. Observed
  spread is *below* the pure-noise expectation: true between-umpire sd **0.000
  K/game**.
- **Statcast, the last gap, failed too** (558 game-days from Baseball Savant), and
  made things worse on the markets actually bet.
- **The pre-registered bar is derived, not guessed, and scale-invariant:** required
  incremental R² = **2π·v²** where v is the per-side vig. σ cancels, so the same bar
  applies to NRFI, F5, full-game runs and props.

**The NRFI anomaly, recorded and NOT acted on.** NRFI is the one cell where the
encompassing coefficient separates from zero (c=+0.939, CI [+0.416, +1.520]). It
fails the pre-registered conjunctive rule and five ways besides - Stage 2 measured
every family on NRFI at ≈0 or negative. It is recorded so it is not rediscovered as
news, not as a lead.

## 6. The soft-book ceiling, and the unresolved tension

**Researched 2026-08-11.** This is the open question everything else waits on.

- **(a) The negative CLV is partly SELF-INFLICTED - the shopped outlier LEADS the
  market.** Splitting comparable legs by whether a best-price book was taken,
  within the shopping era so both groups sit on the same slates: shopped legs moved
  **−0.39pp**. Taking the outlier price means being the one the market then moves
  away from. That turns adverse selection from a trap into a potential signal - be
  *with* the leading price, not merely at the best one.
- **(b) Drift capture is DEAD.** Over **13,034** market instances with ≥2
  comparable snapshots: mean favourite-side drift **+0.04pp** (identical in the
  builder-eligible fav ≥ 0.55 region, n=10,579), against the ~**3.5pp** per-side
  vig any drift strategy must clear. Only 3.9% of instances move enough to matter.
- **(c) THE TENSION - deduplicated realized leg return is POSITIVE and contradicts
  the CLV gate.** Deduped to distinct settled bets (finding 4c's trap), excluding
  voids, at actual booked odds: **+7.6% per leg, n=272 distinct events over 21
  days**, day-clustered. Meanwhile soft-book CLV reads ~2:1 against (n=136,
  coverage 23.2%, 37 toward / 69 against, as of 2026-08-13). **Either three weeks
  of luck, or a biased yardstick.**
- **(d) "Use only the markets that are working" was asked and REJECTED.**
  Per-market, every confidence interval includes zero, and the apparent stars are
  the small-n tails. Forward-picking them is finding 4c's team-tier mirage again.
- **(e) THE DECISION THIS FORCES - a sharp reference price is the single test
  everything else waits on.** It (1) adjudicates (c), (2) replaces a possibly-biased
  yardstick, and (3) turns (a) from a trap into a signal.

**Built the same day** ([spec](superpowers/specs/2026-08-11-sharp-reference-snapshot-design.md)):
`ingestion/theodds_client.py` and migration `011` (append-only `sharp_lines`).
Accumulating; the answer is not yet in.

**Honest ceiling even if everything above works:** six soft books recover +1.34pp
of a ~7.1pp vig. Durable profitability realistically needs **more books or a sharp
reference** (a paid feed), or a genuine predictive signal - and finding 5 closed
that door.

## 7. What the deleted model actually failed at

**Found 2026-07-18.** Kept because it is the reason the product is what it is, and
because the diagnostic is reusable.

**None of the models - player-prop or team-market - could predict individual games
well enough to beat these markets.** They were roughly *calibrated* (got the
average right) and had almost no *resolution* (could not tell games apart). Betting
lives entirely on resolution.

Evidence, all consistent:

- Player-prop `predicted_mean` tracked each player's own season rate with a
  regression slope of only **0.18-0.45** - the model shrank everyone toward the
  league average.
- The F5 team model, built specifically to escape that, failed the same way and
  worse: on a 1,256-game holdout `corr = 0.081`, **R² = 0.007**, with
  `std(predicted) = 0.36` against `std(actual) = 3.31`. It predicted ~5 runs for
  nearly every game.
- Its large flagged "edges" (up to 23% at extreme lines) were **shrinkage
  artifacts** - the model refusing to predict a high total for a slugfest the book
  priced at 7. That is adverse selection, not signal.

**It was a features/data problem, not a model-capacity problem**, and this was
verified rather than assumed: making the F5 model stronger (40 trees/d2 → 300/d5 →
600/d7) made R² **worse** (0.0066 → 0.0048 → 0.0031). A bigger model fit noise.

**The permanent acceptance gate**, should anything ever be proposed for betting
again: demonstrate real per-game **resolution** - `corr`/R² of predicted-vs-actual
well above zero, and `predicted_mean` tracking the market line with slope → 1. Not
merely good calibration or a good Brier score. The diagnostics are: regress actual
on `predicted_mean`; compare `std(pred)` vs `std(actual)`; take the slope of
`predicted_mean` on the book line.
