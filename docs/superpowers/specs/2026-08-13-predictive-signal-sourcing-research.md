# Predictive-signal data sourcing research (§15.9 item 2) — Design Spec

**Date:** 2026-08-13 · **Status:** COMPLETE — **VERDICT: NO-GO** (see §9–§11)
**Mandate:** RESEARCH ONLY, ending in an explicit go/no-go. **No model code. No restoration of the deleted model.**

> **Verdict in one line:** all six candidate signal families are available for **$0**,
> and **none of them survives contact with the market**. Sourcing was never the
> gate; the data simply is not worth what it would cost to use. **Do not rebuild
> the model.**

---

## 1. Why this is being reopened

§15.9 item 2 was shelved 2026-08-08 (*"we'll shelve the model discussions for now and
research the new data that it needs next time"*). The user invoked that "next time" on
2026-08-13. This is that research pass — **not** a model build, and **not** a resume:
the model code and tables were DELETED 2026-08-06 (§16 #3B), so any revival is a
rebuild-from-data decision gated on this document's verdict.

Three independent lines of evidence force the question. All were re-verified live at the
start of this session:

| Evidence | README | Re-verified 2026-08-13 |
|---|---|---|
| Flat-staking the whole 1.4x record makes **+1.7% ROI**, CI **contains** break-even | item 13 | (no change — arithmetic on settled ledger) |
| Market moves **against** our selections | item 12 | `n_compared=141`, **40 toward / 69 against (1.73:1)**, mean **−0.45pp**, coverage 34.1% |
| Pinnacle sharp reference reads **below fair** | item 14e | **8 comparable legs**, coverage **1.9%**, mean `fair_ratio` **0.9583**, **0 above / 8 below** — all `first_inning_runs` unders |

The sharp reference has **not** accumulated since it was recorded — it is still the same
8 legs, because Pinnacle's eu-region MLB prop coverage is stars-only (item 14e finding 2).
It cannot yet adjudicate item 14c's realized-return-vs-CLV tension on its own, and this
document does not lean on it as if it could.

The conclusion that motivates the pass: line shopping across six soft books recovers only
**~1.34pp of ~7.1pp** vig (item 10), and item 14a shows the shopped outlier *leads* the
market, so adding books makes it worse rather than better. **No arrangement of soft books
changes the answer.** A genuine predictive signal is the only remaining lever — that is
item 2, and this is its research pass.

## 2. Binding constraints

These are not design inputs; they are walls.

- **No model code this session.** Measurement instruments live in the scratchpad and are
  thrown away. Nothing enters the repo but this document and a README §15.9 item 2 update.
- **No migrations, no tables, no chain step, no API surface.** `model_prob` stays `None`.
- **§11's acceptance test is permanent** and is the bar any rebuild must clear: corr/R² of
  predicted-vs-actual well above zero **and** `predicted_mean` tracking the book line with
  slope → 1.
- **Tuning is not the problem** (§11, verified 2026-07-18): making the F5 model stronger
  made R² *worse* (0.0066 → 0.0048 → 0.0031). **A finding of "we could tune harder" is a
  no-go, not a go.** So is "collect more line history and retry."
- **§15.8 guardrails hold regardless** of the verdict: rank on devig `market_prob`,
  `market_prob ≥ 0.55`, 2–4 legs, paper-only, and no +EV / edge / value language anywhere.
- **Additive-only, mlb-default.** `/parlay-builder/saved`, `/box-scores` and `/games` are
  the Budgerr contract and stay byte-identical. This pass touches none of them.

## 3. Sourcing — what the live probe already established

Every claim below is from a live probe on 2026-08-13, not from documentation. This matters:
the umpire is **absent** from `gameData.officials` (empty array) and present in
`liveData.boxscore.officials` — reading the docs would have concluded "not available."

**Join key:** `game_pk = game_id − 100_000_000`. Verified: `100824240` → Cleveland Guardians
@ Detroit Tigers, 2026-08-11.

**Endpoint:** `statsapi.mlb.com/api/v1.1/game/{pk}/feed/live` with `?fields=` pruning —
**13.5 KB vs 814 KB unpruned, a 60× reduction**. Free, key-less, no quota. Same provider the
`stats`/`linescores` chain steps already use, so licensing terms are unchanged from current
ingestion.

| Signal family | Path | Probe result |
|---|---|---|
| Park factors | `gameData.venue` | `{id: 2394, name: Comerica Park}` — join key present |
| Weather | `gameData.weather` | `{condition, temp, wind}`; wind carries **direction** (`"1 mph, In From RF"`), the part that matters |
| Umpire | `liveData.boxscore.officials` | **`Home Plate: Sean Barber`** + three bases |
| Confirmed lineups | `boxscore.teams.*.battingOrder` | 9 player ids per side — real batting-order slots |
| Deep pitcher/bullpen | `boxscore.teams.*.pitchers` | full pitcher sequence in appearance order |
| First pitch / day-night | `gameInfo.firstPitch`, `datetime.dayNight` | `22:40Z`, `night` |

**Historical depth — verified across all three seasons** (five sampled games, one per
season-segment; every field populated on every one):

```
2024-04-15  venue=680  temp=50  wind='0 mph, Calm'        HP=Jonathan Parra    order=9  pen=4
2024-09-01  venue=15   temp=78  wind='0 mph, None'        HP=Junior Valentine  order=9  pen=4
2025-05-20  venue=31   temp=66  wind='6 mph, In From LF'  HP=Gabe Morales      order=9  pen=3
2025-08-15  venue=7    temp=91  wind='10 mph, R To L'     HP=Todd Tichenor     order=9  pen=5
2026-04-10  venue=32   temp=65  wind='0 mph, None'        HP=Mark Wegner       order=9  pen=6
```

**So five of the six candidate families cost $0 and are already licensed.** The sourcing
half of item 2 is substantially answered, which shifts the pass's centre of gravity onto
*"what would it buy"* — the measurement.

**Still unprobed:** Statcast / pitch-level (Baseball Savant). This is the one genuine
sourcing unknown remaining, and it is the family most likely to carry signal the others
lack. It was **not** in the README's original five-candidate list; it is added here because
omitting the best free MLB data source would make a no-go unfalsifiable.

## 4. The constraint that shapes the whole design

| Data | Window | n |
|---|---|---|
| Outcomes (`runs`, `runs_f5`, `runs_inning_1`), per team | 2024-03-20 → now | **6,673 games** |
| Player box scores | same | 134,647 batting / 56,974 pitching rows |
| `starter_player_id` | same | 6,729 games |
| **Stored book lines** (`game_lines`) | **2026-07-16 → now** | **322 F5 / 336 NRFI games** |
| Stored prop lines | 2026-07-16 → now | 83,159 rows / 336 games |

**The decisive test is the underpowered one.** "Does weather predict F5 runs?" is
answerable at n=6,673 and is *not the question* — the book already prices weather. The
question is whether a signal predicts runs **beyond the line**, and there are only four
weeks of lines. The design must not let the well-powered test stand in for the decisive one.

## 5. Method — four stages

### Stage 0 — join spine (scratchpad)

Pull all 6,673 MLB `feed/live` records via the pruned endpoint (~87 MB), cache to the
scratchpad so re-runs are free, and build a per-game table: venue, first pitch, day/night,
temp, wind speed, wind direction, home-plate umpire, both batting orders, both pitcher
sequences. Join to the existing `runs` / `runs_f5` / `runs_inning_1` / `starter_player_id`
rows and to `player_game_stats`.

Politeness: sequential-with-small-delay or low concurrency; this is a free public API and
the pull is a one-off.

**Wind direction needs park orientation to be meaningful** — "In From RF" is only
interpretable relative to the stadium. Derive a per-venue orientation mapping, or encode
wind as the categorical MLB already provides (`In From`/`Out To`/`L To R`/`R To L`/`Calm`),
which is already park-relative and needs no extra source. Prefer the categorical: it is
free and avoids a fabricated-precision orientation table.

### Stage 1 — sourcing dossier

Per family: availability (live-probed), cost (free vs paid tier, with real prices), historical
depth (verified, not advertised), licensing, and the join cost. Statcast gets a real probe
here — endpoint, coverage for our `game_pk` range, rate limits, terms.

### Stage 2 — Tier 1 ceiling (n = 6,673)

For each family, measure **incremental R² over a rolling-form baseline** — not raw R². Raw
correlation is misleading because most of these signals are proxies for team quality, which
the baseline already carries.

- **Baseline:** team rolling scored/allowed + starter form — deliberately the *same feature
  class* the deleted F5 model used, so the comparison is "what does the new data add to what
  we already had," which is exactly item 2's question.
- **Targets, run-environment track:** `runs_inning_1`, `runs_f5`, `runs`.
- **Targets, player-prop track:** the 13 stats, prioritizing `batter_strikeouts`,
  `pitcher_strikeouts`, `hits`, `total_bases`, `home_runs`.
- **Errors clustered by day** — item 14c established day-clustering as this repo's standard,
  because legs on the same slate are not independent.
- Instruments are throwaway scratchpad OLS/GBM. They are measuring devices, not deliverables.

**Two a priori favourites, flagged now so the result is interpretable either way:**
**umpire → strikeouts** (best-documented umpire effect in the literature; we have three
seasons of both `batter_strikeouts` and `pitcher_strikeouts`, so it is well-powered) and
**batting-order slot → counting props** (mechanically drives plate appearances, which drives
every counting stat). If *these* come back empty, the no-go is strong.

### Stage 3 — Tier 2 validity (n ≈ 322 lined games)

Two questions on the window where real book lines exist:

1. **Does the Stage-2 baseline track the book line with slope → 1?** This is §11's permanent
   acceptance test, used here as a *validity check on the baseline itself*. A baseline that
   cannot track the line is not a credible thing to measure "incremental over."
2. **Does any family add beyond the line?** Regress the outcome's residual-from-market on
   the candidate signal. A signal the market already prices buys nothing, no matter how
   large its Tier 1 R².

Report `n` and coverage as first-class output, per item 12's line-movement precedent. A low
comparable rate is itself a finding, and an underpowered null must be reported as
underpowered, not as a null.

### Stage 4 — verdict

Go / scoped-go / no-go, with costed options for anything paid that surfaced. Paid plans are
escalated to the user as options, never assumed.

## 6. Pre-registered decision rule

Stated **before the data votes**, per item 14d's own standard.

**Derivation of the bar — and it is scale-invariant.** To move a leg's probability by the
per-side vig `v`, the conditional mean must move by `Δ = v / density`. For a roughly normal
outcome the peak density is `1/(σ√(2π))`, so `Δ = v · σ · √(2π)` and the incremental R²
required is:

```
R²_required = (Δ / σ)² = 2π · v²
```

**σ cancels.** The bar is the same for NRFI, F5, full-game runs and player props — it depends
only on the vig being overcome. At `v = 3.5pp`: **2π × 0.035² = 0.0077, i.e. ~0.8%.**

Re-derived on our own 6,673 games rather than cited (the §11 figures are confirmed, not
assumed):

| Target | n | mean | σ | peak density | R² to clear 3.5pp |
|---|---|---|---|---|---|
| `runs_inning_1` (game total) | 6,673 | 1.04 | 1.46 | 0.273/run | 0.77% |
| `runs_f5` (game total) | 6,673 | 4.98 | **3.27** | 0.122/run | 0.77% |
| `runs` (game total) | 6,673 | 8.88 | 4.48 | 0.089/run | 0.77% |

(The F5 mean of **4.98** is itself the §11 shrinkage finding restated: the deleted model
predicted ≈5 runs for nearly every game — it was reproducing the unconditional mean.)

So ~0.8% is break-even and a real margin wants 2–3× that. The bar is set at **1%**: above
break-even, below the point where only an implausibly strong signal qualifies.

| Verdict | Condition |
|---|---|
| **GO** | A family adds **≥1% incremental R²** over baseline, day-clustered-significant, **and** is not absorbed by the book line in Stage 3. |
| **SCOPED GO** | Meets the full GO condition, but on a narrow market only (e.g. umpire → `pitcher_strikeouts`). Names that market; does not generalize beyond it. |
| **NO-GO** | Everything else. This explicitly includes the **0.5–1% band** — real but insufficient to clear vig — and any family that clears 1% on Tier 1 but is absorbed by the market line on Stage 3. Below 0.5% is recorded as "not close," which is a stronger form of the same verdict. |

**Explicitly not a go:** "we could tune harder" (§11 already falsified it), "collect more
line history and retry," or a large Tier 1 R² that Stage 3 shows the market already prices.

**A GO does not start a rebuild.** It hands the user a costed decision. The rebuild itself is
a separate, later call that the user makes.

## 7. Deliverable

1. This document, updated in place with findings and the verdict.
2. A README §15.9 item 2 rewrite recording the outcome — same commit, pushed.

Nothing else lands. No repo code, no migration, no endpoint, no chain step.

## 8. Risks

- **Underpowered Stage 3.** 322 games may not distinguish a small real effect from zero.
  Mitigation: report it as underpowered rather than as a null, and let Stage 2 carry the
  power while Stage 3 carries the validity.
- **The instrument drifting into a model.** Mitigation: scratchpad only, thrown away, and
  the deliverable is prose plus numbers — there is no artifact to accidentally deploy.
- **Confirmation pressure toward a "go."** Three sessions of negative findings create an
  appetite for good news. Mitigation: the bar is pre-registered above, before any fitting.
- **Survivorship in the lined window.** The 336 lined games are the slates the builder
  actually ran on, not a random sample. Note it; do not correct for it silently.

---

# FINDINGS (research executed 2026-08-13)

## 9. Stage 1 — sourcing: all six families cost $0

The sourcing question, which item 2 framed as the main unknown, turned out to be
nearly a non-question. Five families live in **one free, key-less endpoint the
project already calls** — `statsapi.mlb.com/api/v1.1/game/{pk}/feed/live` — at
**13.5 KB/game** with `?fields=` pruning (60× smaller than the 814 KB raw feed).

The full 3-season spine (**6,682 games**) pulled in ~4 minutes for ~87 MB, with
**100% field population on every field in every season**: venue, temperature, wind
speed, wind direction, condition, home-plate umpire, day/night, first pitch, and
full 9-man batting orders on both sides. 41 venues, **99 distinct home-plate
umpires**, 11 wind-direction categories.

- **Park factors** are **derived from our own 6,682 games**, not sourced. This is
  better than an external feed, not a fallback: nobody publishes **F5 or
  first-inning** park factors, and those are the markets the builder actually bets.
  The derivation validates against known baseball reality — Coors Field F5 = **1.32**,
  T-Mobile Park = **0.79**, Sutter Health Park = **1.23**.
- **Statcast** is free and needs no key. Raw `launchSpeed`/`launchAngle` and
  `startSpeed`/`spinRate` are already inside the same `feed/live` we call
  (independently verified); Savant is needed only for computed metrics (xwOBA,
  barrel rate), available as one-request player-season leaderboard CSVs.
- **Licensing** is unchanged from what the project already operates under (MLB
  personal/non-commercial). Fine for internal paper research; not for anything
  productized or redistributed.

**No paid option surfaced for any family. There was nothing to escalate.**

### Traps found while probing (all real, all silent)

- The umpire is in `liveData.boxscore.officials`. **`gameData.officials` is an empty
  array** — reading the docs yields "not available".
- Baseball Savant's CSV export **silently caps at ~25,000 rows**: HTTP 200,
  well-formed CSV, just short. Same failure class as the "100% coverage" metric
  that compared prices to themselves.
- A wrong Savant leaderboard field name **returns 200 with blank values**, not an error.
- Savant catcher-framing CSV exports have **blank id/name columns** — metric exists,
  export is broken.
- **The `+100,000,000` id-offset trap bit this analysis directly.** StatsAPI
  `battingOrder` returns RAW player ids; our DB stores MLB players offset by +100M
  (`ingestion/config.py`). The un-offset join matched **zero** rows, produced an
  all-NaN feature, and the lineups family was silently dropped as "no usable
  features" on the first run. After the fix the join matches **90.6%** of 120,276
  slots — and lineups became the *strongest* family in the study. Every id join in
  this research prints a match-rate diagnostic because of this.

## 10. Stage 2 — incremental R² over a rolling-form baseline

Train 2024+2025, score 2026. Ridge primary, GBM as nonlinearity check. 95% CI from
day-clustered block bootstrap. Baseline = team rolling scored/allowed + starter form
(deliberately the deleted model's own feature class).

**Baseline out-of-sample R²: 0.0051 (NRFI) / 0.0110 (F5) / 0.0108 (full-game)** —
an independent replication of §11's F5 finding (recorded there as R²=0.007).

### Run environment (n_test = 1,747)

| Family | NRFI | F5 runs | Full-game runs |
|---|---|---|---|
| **lineups** | −0.12pp | **+1.64pp** `[+0.68,+2.57]` | **+2.19pp** `[+1.29,+3.10]` |
| **park** | +0.07pp | **+0.87pp** `[+0.18,+1.56]` | **+1.00pp** `[+0.39,+1.63]` |
| weather | +0.12pp | +0.28pp (ns) | +0.90pp `[−0.04,+1.80]` |
| **umpire** | +0.01pp | +0.02pp | +0.05pp (ns) |
| bullpen | −0.04pp | −0.10pp | −0.16pp |
| ALL | −0.58pp | +2.09pp | **+3.23pp** `[+1.17,+5.42]` |

### Player props (n_test 29.9k–36.1k batters; 3,072–3,201 starters)

Baselines: batter_strikeouts +0.0472, hits +0.0249, total_bases +0.0207,
home_runs +0.0121, pitcher_strikeouts +0.1442.

| Family | batter K | hits | total bases | home runs | pitcher K |
|---|---|---|---|---|---|
| **umpire** | **−0.0001** | −0.0000 | −0.0000 | −0.0000 | **−0.0003** |
| lineup slot | +0.0013 | +0.0017 | +0.0025 | +0.0012 | n/a |
| park | +0.0015 | +0.0023 | +0.0020 | +0.0009 | +0.0041 |
| weather | −0.0001 | +0.0009 | +0.0018 | +0.0016 | −0.0015 |
| opp quality | +0.0049 | +0.0015 | +0.0015 | +0.0007 | **+0.0180** |

**Nothing clears the 1% bar on a CI-lower-bound basis anywhere in the prop track.**
The team-level signal does not carry down to player level: `park` is +1.00pp on
full-game runs but +0.09–0.41pp on props; `lineups` is +2.19pp there but
+0.12–0.25pp here. The player's own rolling form already absorbs it.

**The GBM never beat Ridge** on any target (full-game 0.0295 vs 0.0345; F5 0.0042 vs
0.0216; pitcher K 0.1497 vs 0.1637) — independent confirmation of §11's
"a stronger model made R² worse."

### The umpire is not weak — it is absent

Variance decomposition over 6,682 games and 84 umpires with ≥40 games:

| | |
|---|---|
| Observed sd of per-umpire mean strikeouts | **0.476** |
| sd expected from sampling noise if all umpires were **identical** | **0.480** |
| Variance-corrected true between-umpire sd | **0.000 K/game** |
| Implied maximum R² from umpire identity | **0.00000** |

The observed spread is *below* the pure-noise expectation. CB Bucknor's 17.6 K/game
against James Jean's 15.3 is entirely explained by which games they were assigned.
**Four independent measurements agree** (team runs +0.0005, batter K −0.0001,
pitcher K −0.0003, variance decomposition 0.00000). The best-documented umpire
effect in baseball does not exist in three seasons of our data.

## 11. Stage 3 — the decisive test: does anything beat the MARKET?

Forecast-encompassing regression `y = a + b·MARKET + c·f` on the lined window. The
market basis is deliberately **flexible** (line, p, p², p³, logit p, −ln(1−p)) so
that no result can come from functional-form arbitrage — for a count outcome the
market-implied mean is convex in p, and a linear-only basis manufactures fake edge.

| Target | n | R² market | R² market+ours | c | 95% CI | Verdict |
|---|---|---|---|---|---|---|
| **F5 runs** | 294 | +0.0518 | +0.0554 | +0.260 | `[−0.224,+0.781]` | **market encompasses** |
| **pitcher K** | 530 | **+0.2381** | +0.2406 | +0.311 | `[−0.050,+0.682]` | **market encompasses** |
| NRFI | 306 | +0.0261 | +0.0523 | +0.939 | `[+0.416,+1.520]` | anomaly — see below |

Two results deserve emphasis:

- **`park` and `lineups` cleared Stage 2 and are fully absorbed by the market.**
  Predicted in advance, and it happened: they are the two variables a book prices
  most explicitly — lineups are *literally why* books hold props until lineups post.
- **On pitcher strikeouts the market beats our best model outright** — market alone
  R² **0.2381** vs our model's **0.1610**. `opp_quality`'s +1.80pp over our own
  baseline collapses to **+0.25pp** over the market, CI containing zero.

### The NRFI anomaly, and why it is NOT a go

NRFI is the one cell where `c` separates from zero. It fails the pre-registered rule,
and the reason matters:

1. **It fails the first conjunct.** Stage 2 measured every family on NRFI at ≈0 or
   negative (`ALL` = **−0.58pp**). The GO condition requires clearing the bar over
   baseline **and** surviving the market. It never cleared the bar.
2. **The decomposition inverts the innocent explanation.** Baseline-only is
   encompassed (c=+0.607, CI `[−0.774,+2.077]`); the effect appears only when the new
   families are added — the same families Stage 2 measured as *harmful* on this target.
3. **The model is worse overall on the very window that shows the effect**: raw OOS
   R² is **−0.0041** across full-2026 but **+0.0134** on this 4-week slice. Features
   that hurt prediction in general cannot be a genuine source of market-beating
   information on a subset. That is a lucky window.
4. **It contradicts the sharp reference.** §15.9 item 14e reads **8 of 8**
   `first_inning_runs` legs *below* Pinnacle fair (mean `fair_ratio` 0.9583). If we
   truly had NRFI edge, the sharp book should agree with us more than the soft books
   do, not less.
5. **Multiplicity.** This is one significant cell among ~30 tested.

Recorded as an **anomaly for a forward-only, pre-registered re-test**, never acted on.
This is item 14d's "small-n tails" trap in new clothes, and item 13's error in a new
costume; the conjunctive rule was pre-registered precisely to catch it.

## 12. VERDICT — NO-GO

**No family, on any target, clears the pre-registered bar and survives the market.**

| Family | Outcome |
|---|---|
| umpire | **Dead.** Effect is absent, not weak — true between-umpire variance is 0.000. |
| bullpen | **Dead.** Negative incremental R² on every run-environment target. |
| weather | Below bar except full-game runs, where the CI touches zero. |
| park | Clears Stage 2 (+1.00pp) → **fully absorbed by the market.** |
| lineups | Strongest in Stage 2 (+2.19pp) → **fully absorbed by the market.** |
| Statcast | Free and available; not reached — every cheaper family was absorbed first, and season-aggregate leakage makes it usable only as a lagged feature. **The one honest gap in this pass.** |

**This is a no-go on the merits, not on cost.** Every family is free and already
licensed; the sourcing question item 2 was written to answer came back entirely
positive. The data exists, we can have all of it for nothing, and it still does not
beat the book. That is a much stronger result than "we couldn't afford the data."

**§11's acceptance test is not close to being met.** Our F5 forecast tracks the book
line with slope **+0.460** — better than the deleted model's 0.185, still nowhere near
1 — and on NRFI the slope is **+0.007**, i.e. our forecast and the market are
essentially unrelated.

**Recommendation: do not rebuild the model.** The builder remains the product;
`model_prob` stays `None`. Per §16 nothing live depends on this, so the cost of this
verdict is zero.

### What would change the answer

Stated so a future session does not re-run this pass hoping for a different result:

1. **Statcast measured properly** as a lagged rolling feature (the honest gap above).
   Expected to fail for the same reason everything else did — it is the *most* widely
   consumed public baseball data, so it is the most likely to be priced already.
2. **A market that is actually soft.** Every test here says these six books price
   park, lineups, weather and pitcher quality correctly. The binding constraint is
   §15.9 item 12's ceiling, not our feature set.
3. **Not** more tuning (§11 falsified it, and this pass re-falsified it — the GBM
   lost every time), and **not** more line history to re-test the NRFI anomaly. Both
   were pre-registered as no-go outcomes.
