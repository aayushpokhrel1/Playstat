# Product

## Register

product

## Platform

web

## Users

Primarily Aayush himself - a solo bettor/analyst who opens Playstat before a night
of games to see what the night's safest constructions look like and how the paper
record is doing. He's fluent in the numbers (implied probability, de-vigging,
joint probability, calibration) and doesn't need things explained. Secondary,
longer-term: a friend or two, and possibly a wider audience if this opens up
beyond personal use - so the UI shouldn't lean so far into insider shorthand that
a newcomer can't eventually follow it.

## Product Purpose

A dashboard for an honest constructor: it turns tonight's sportsbook prices into
the least-bad parlay at a chosen payout, shows the joint probability of that
construction front and centre as the risk it is, and keeps a paper ledger that
says plainly whether the approach makes money. It replaces querying the database
or reading raw builder output by hand.

It does **not** present an edge, a value bet, or a disagreement with the market.
There is no model to disagree with, and the measured position is that no edge
exists here (see "The honest position", below). A screen that implied otherwise
would be the one way this product could actively mislead its user.

## Positioning

The one screen where tonight's prices become a construction you can judge -
honest about its own risk, and honest that safety is not profit. Not a stats
browser, not a tipster, not a raw data dump.

## Brand Personality

Terminal / analyst-native crossed with sportsbook-modern, expressed with quiet,
editorial restraint: Bloomberg-style density and numerical credibility,
sportsbook-grade confidence and glanceability, but without the sportsbook's
gradient-and-hype visual language. Precision over decoration.

## Anti-references

The generic SaaS-dashboard-in-a-box look (card grids, pastel KPI tiles,
rounded-everything, a stray gradient accent) - it undersells how much rigor is
behind the numbers. Also avoid actual sportsbook chrome (odds-app skins, promo
banners, gamified color) - the tone is analyst, not marketing.

## Design Principles

Density over decoration - the interface earns its data density instead of padding
it out with card chrome.

Numbers earn trust before they earn attention - a figure reads with the context
that makes it honest (joint probability, sample size, what the price already
demands), not as a bold coloured number on its own.

Speed and depth share a screen - the same view supports a five-second scan of
tonight's cards and a longer dig into the record, without forcing a choice.

Confidence without hype - visual polish signals credibility, not promotion.

Built to open up - solo-use today, but legible enough that a friend or a future
user could read it without a walkthrough.

## Accessibility & Inclusion

No formal compliance target while this stays personal-use, but body text should
still clear AA contrast (4.5:1) and the UI should support both light and dark
(`prefers-color-scheme`), leaning dark given the terminal-native personality -
since it's expected to open up beyond solo use later.

---

## The honest position

**This is not a money-making system, and nothing in this repo may claim to be
one.** The position below is measured, not assumed; the arithmetic and the ledger
tests are in [docs/FINDINGS.md](docs/FINDINGS.md).

A sportsbook prices both sides to sum to ~107%; that ~7% is its margin, and you
pay it on every leg. Parlaying **multiplies** it - roughly −7% at one leg, −14%
at two, −26% at four. Ranking by *safety* picks the least-bad of a pool of
negative-value bets; it does not create value. Shopping six books recovers only
about **1.3 points of the ~7**.

A high hit rate is free and is not the goal: the price already demands it. The
1.4x tier's ~76% hit rate sits against a ~74% break-even, and asking the builder
for more safety makes the shortfall *worse*, monotonically, because every added
favourite multiplies the vig while the payout collapses toward 1.0.

The one thing that could change this is a genuine mispricing, and the industry
test for it is **closing-line value**. As of the last measurement (2026-08-13)
the market has moved *against* our selections roughly **2:1** (n=136). A
counter-signal exists and is unresolved: distinct settled legs have returned
**+7.6%** at booked odds over 21 days, which either is three weeks of luck or
means the soft-book yardstick is biased. **Adjudicating that requires a sharp
reference price**, which is built and accumulating. Everything downstream waits
on it.

So Playstat is best described as an **honest constructor and a measurement
sandbox**: it is very good at telling the truth about a betting strategy, and the
truth right now is that this one has no edge.

## Guardrails (binding - do not violate)

These are user-confirmed and load-bearing. Several exist because a specific piece
of work went wrong without them.

1. Rank **only** on de-vigged market probability. Never on a model.
2. **No "+EV" / "edge" / "value" / "beat the market" claims** anywhere - UI, API
   payloads, or stored JSON. This includes naming: anything shipped about price
   changes is named for *movement*, not value.
3. Always surface joint probability prominently - it *is* the risk.
4. Favourite-side legs only, `market_prob >= 0.55`, 2-4 legs.
5. Across-game only, except the one deliberately labelled same-game tier.
6. **No real-money deployment.**
7. **Do not claim a closing line.** The last pre-start snapshot lands a median
   ~100 minutes (worst ~150) before first pitch. A field named `closing_odds`
   would describe something that does not exist here.

## Decisions

### The product is the builder, and the model is gone

**User-confirmed 2026-07-28, reaffirmed 2026-08-08.** The MLB prediction model
and its edge betting were shelved (2026-07-29), then its code and tables were
**deleted** (2026-08-06), along with the dormant F5 team-market model.

**Why:** the models were roughly *calibrated* but had almost no *resolution* -
they could not tell games or players apart, and betting lives entirely on
resolution. Model-ranked bets ran **−57% ROI**. Making the model bigger made it
**worse** (R² 0.0066 → 0.0031 across 40→600 trees), which proved it was a
features/data problem, not a capacity problem.

**Deleted rather than kept frozen** because two stores of the same idea drift, and
a frozen model serving stale rows invites someone to trust it. `devig` /
`odds_to_probability` were extracted to `optimizer/devig.py` first - the builder
ranks on de-vigged market probability, so those were never model-specific. A full
`pg_dump` of the six dropped tables was taken before the migration.

**Model discussion is shelved, not scheduled.** Do not reopen it without new
data, and read the next decision first.

### Rebuilding the model: NO-GO

**Researched 2026-08-13, closed 2026-08-14.**
[Full findings](docs/superpowers/specs/2026-08-13-predictive-signal-sourcing-research.md).

All six candidate signal families - park factors, weather, umpire, lineups,
pitcher/bullpen depth, and Statcast - were sourced and tested against a
pre-registered bar. The sourcing question came back entirely positive (five
families live in one free, key-less endpoint already called, at 13.5 KB/game).
The signal question came back negative: the market **encompasses** our forecasts
on every market actually bet, and Statcast, the last gap, failed too.

Two results worth keeping because they are absolute rather than weak:

- **The umpire effect does not exist.** Over 6,682 games and 84 umpires with ≥40
  games, the observed spread of per-umpire mean strikeouts is *below* what pure
  sampling noise predicts if all umpires were identical. True between-umpire sd:
  **0.000 K/game.**
- **The bar is scale-invariant.** Required incremental R² is `2π·v²` where `v` is
  the per-side vig; σ cancels, so the same bar applies to NRFI, F5, full-game runs
  and props alike.

**Cost of the verdict: zero** - nothing live depended on it. **The parlay builder
remains the product, full stop.**

**What would change the answer:** a market that is actually soft. The binding
constraint is the soft-book ceiling, not the feature set. Explicitly **not**: more
tuning (falsified twice), more line history to re-test the NRFI anomaly, or more
signal families.

### Builder design decisions (user-confirmed 2026-07-18)

Full design: [low-risk parlay builder spec](docs/superpowers/specs/2026-07-18-low-risk-parlay-builder-design.md).
**Do not relitigate these.**

| # | Decision | Choice |
|---|---|---|
| 1 | Interaction | **Two-axis.** Pin either target payout **or** a minimum joint-probability floor; the builder bounds the other. Both always surfaced. |
| 2 | Build order | Engine + API first, dashboard second. |
| 3 | Same-game legs | Across-game only in v1; same-game shipped later as a separate labelled tier. |
| 4 | Model's role | Non-authoritative display context only, labelled "not used for ranking". |
| 5 | Nightly step | The builder **replaces** the OOM-dying `optimizer.parlay` step. |
| 6 | Leg menu | All markets, favourite-side only, per-leg de-vigged floor ≥ 0.55. Price decides what is safe - no hardcoded stat blacklist. |
| 7 | Leg count | 2-4 legs, prefers fewest. |

Nightly defaults: **~1.4x "safe"** and **~2.0x "reach"**, so a paper record builds
at both risk levels.

Two later corrections that are easy to get wrong again:

- **A pinned target payout is a FLOOR, not the centre of a tolerance band.** A
  symmetric band ranked by joint probability always returns the band's bottom
  edge, because joint probability falls monotonically as payout rises.
- **A high hit rate is a slider, not an achievement** (see "The honest
  position"). Do not treat a request for more safety as a request for a better
  bet.

### Deliberate non-bugs

Recorded because each one looks like a defect and is not:

- **The morning card and the confirmed-lineup card can both be saved for the same
  construction**, booking two paper bets. `construction_signature` dedupe is
  scoped per `(kind, class, sport)` on purpose, so the two classes can be compared
  on void rate and ROI - which is the only rigorous way to prove the lineup fix
  worked.
- **Reported ROI should FALL as voids drop.** A void removes a leg, which removes
  one ~7% vig multiplication; cards that lost legs returned +11.2% against +6.1%
  for intact cards. Fixing voids makes the measured number worse and the
  measurement truer. **That is success, not regression.**
- **Team legs almost never surface.** This is structural, not stale data: team
  markets price near coin-flip, so ~2 of 40 clear the 0.55 floor. The floor is
  binding (guardrail 4), so the fix was a separate labelled team tier, not a
  lower floor.

## Roadmap

Ordered. Everything here is gated on the item above it where it says so.

1. **Adjudicate the CLV tension with a sharp reference price.** Built and
   accumulating (`ingestion/theodds_client.py`, migration `011`). This is the
   single test everything else waits on: it resolves whether the +7.6% realized
   leg return or the 2:1-against soft-book CLV is the honest signal, and it
   replaces a possibly-biased yardstick.
2. **+EV selection, if and only if item 1 clears.** The design is worked out and
   deliberately **not built**: move the EV test from the staking stage to the
   selection stage (the system already computes it - `optimizer/stake.py` stakes
   zero when `p·d ≤ 1`). Measured volume: ~14 legs per slate are +EV at the
   shopped price and clear the 0.55 floor. **The product changes shape if this
   ships** - a 2-leg +EV card is ≈ +2.9% EV but hits only ~42%, against today's
   1.4x card at ~75% and ≈ −13.7% EV. "Win most of the time" and "make money"
   pull in opposite directions, and shipping this is choosing the second.
3. **Finish NFL** - game-markets tier + settlement, then chain + dashboard.
4. **Flip the gated sports on** when the paid plans are bought (see below).

### Multi-sport status

The builder core is sport-agnostic and sport-parameterized (`--sport`, `sport` in
the legs blob, `?sport` filter, default `mlb`).

| Sport | State | Gate |
| --- | --- | --- |
| **MLB** | Live and settling daily - the reference implementation | - |
| **NFL** | Odds ingestion + player-prop tier built | Game-markets tier, settlement, chain + dashboard; seasonal |
| **NBA** | Both tiers built, structurally verified | Paid API-Sports plan (free tier caps at seasons 2022-24) |
| **MLS** | Built and verified on free 2022-24 data | Paid API-Football plan (~$19/mo Pro) |
| **UCL** | Built and verified (`sport='ucl'`, offset +500M) | Same paid plan as MLS |
| **NHL** | Built and verified - the first live-for-free expansion | None; does real work at puck-drop (~Oct) |
| **EPL** | Not built | Not on the free SGO tier at all; needs a paid plan first |

Every new sport follows the same discipline: brainstorm → spec → plan → build →
verify live → merge, with the guardrails above holding throughout.

### Deferred, with the reason

- **Same-game correlation beyond the NRFI+F5 tier** - books reprice correlated
  legs, so there is no mispricing to harvest.
- **Alerting on fresh prices** - the free odds quota does not support the pull
  frequency that would make alerting useful, and the paid plan is not justified
  while no edge exists.
- **A CLV panel for the dashboard or a consumer** - the plumbing is specified and
  the consumer shipped its half, but the conclusions stay dark until item 1
  resolves. A panel now would be a panel with no honest content.
- **Real-money deployment** - guardrail 6, and the measured position gives no
  reason to revisit it.
- **Three-way (`ml3way`) soccer markets** - do not fit the builder's two-sided
  geometry. Soccer runs on its two-sided markets only.
