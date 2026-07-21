# Low-Risk Parlay Builder — Design (2026-07-18)

**Status: DESIGNED, NOT YET BUILT.** All decisions user-confirmed 2026-07-18. Next step: `writing-plans` → subagent-driven build. Stage 1 = engine + API; stage 2 = dashboard.

## 1. Goal (reframed by user, 2026-07-18)

Build a low-risk parlay **builder**, not a market-beating model. Construct a near-double / double (~2x) payout from multiple low-risk legs, mixing MLB **player props** and **team props** (NRFI = 1st-inning runs, F5 = first-5-innings runs).

The user explicitly is **NOT** asking to beat the market and accepts this may not be +EV. The job is *the safest construction of a ~2x parlay, surfaced honestly.*

This is a deliberate response to README §11's fundamental finding: the models lack per-game resolution and cannot beat these markets. The builder therefore does not depend on them.

## 2. The honesty core (do not relitigate)

- In a devigged (efficient) market, a parlay's true joint probability is roughly **1/payout** regardless of how it is constructed. A 2x parlay is **~48–50% to hit** — a coin flip.
- Every extra leg adds another **vig bite**, lowering the real probability. So at a fixed payout, **fewer legs is strictly safer**.
- "Low risk" and "2x" pull against each other. The builder's entire job is to make that tradeoff **visible** and pick the least-bad point on it. For genuinely low risk (75%+), the honest target is **~1.3–1.5x**, not 2.0x.
- Rank everything by **MARKET-implied (devigged) probability**, never model probability. Per §11 the models are roughly calibrated but have almost no resolution, and they *overstate the safety of heavy-favorite legs* — exactly the legs a 2x builder leans on. The book's devigged price is the best-calibrated probability available.

## 3. Decisions (all user-confirmed)

| # | Decision | Choice |
|---|---|---|
| 1 | Interaction model | **Two-axis.** User pins either target payout **or** a minimum joint-probability floor; the builder optimizes/bounds the other. Both always surfaced. |
| 2 | Build order / surface | **Engine + API first, dashboard second.** Two reviewable stages. |
| 3 | Same-game legs | **Across-game only for v1.** Legs from different games → independent joint = product. Same-game deferred to future work. |
| 4 | Model's role | **Displayed as non-authoritative context only.** Rank/filter purely on market prob; show `model_prob` labeled "not used for ranking." |
| 5 | Nightly step + tracking | **Builder replaces the OOM-dying `optimizer.parlay` step.** Daily chain writes its picks; existing `settle.py` scores them. |
| 6 | Leg menu | **All markets, favorite-side only, per-leg devigged-probability floor ≥ 0.55** (tunable). Market price decides what's safe — no hardcoded stat blacklist. |
| 7 | Leg count | **2–4 legs, prefers fewest.** Joint-prob ranking surfaces 2-leg constructions first. |

Nightly default targets: **~1.4x "safe"** and **~2.0x "reach"**, so a paper record accumulates at both risk levels.

## 4. Critical data-layer finding

`edges.side` / `edges.implied_prob` store the side the **model** prefers (chosen by max edge, `modeling/edges.py` L92–95) — which may be an underdog the model likes. The builder wants the **favorite** side, which is a *market* question.

**Therefore: the builder devigs the raw two-sided odds itself** from `prop_lines` / `game_lines` (using the existing `modeling.edges.devig()` / `odds_to_probability()` helpers) and picks the favorite. It reads `edges` / `game_edges` **only** to left-join `model_prob` as display context.

> `edges` is model-centric. The builder is market-centric. Do not rank on `edges.implied_prob`.

## 5. Architecture (Approach 1 — new unified module)

New module **`optimizer/builder.py`**, market-centric.

### 5.1 Leg loading
- Query latest `prop_lines` (player props, 13 MLB stat types) and latest `game_lines` (team NRFI + F5) — both sides present, one row per market per latest pull (same `DISTINCT ON ... ORDER BY pulled_at DESC` pattern as `modeling/edges.py:latest_prop_lines`).
- Devig each market's two sides; take the **favorite** (higher devigged prob).
- Keep the leg only if favorite prob ≥ **floor (default 0.55)**.
- Skip one-sided lines (~8% of live MLB lines can't be devigged) — same guard as `edges.py`.
- Restrict to games not yet `FT`.
- Normalize player and team legs into **one common leg schema**:
  `{game_id, kind: 'player'|'team', label, stat_type|market, side, line_value, american_odds, decimal_odds, market_prob, model_prob (nullable, context only)}`

### 5.2 Search
- Across-game combinations of size **2–4**. Reuse the same-game exclusion logic (a combo with two legs sharing a `game_id` is skipped) — this is what makes the independent product valid.
- Per combo: `combined_odds = Π decimal_odds`, `joint_prob = Π market_prob`.
- Rank on **market** joint probability (the existing `find_combinations` ranks on `model_prob` — either generalize it to take a probability key or write the search fresh in `builder.py`; the tested same-game-exclusion behaviour must be preserved either way).

### 5.3 Two-axis filter and rank
- Inputs: `target_payout` + `tolerance` band, and/or `min_joint_prob` floor.
- Pin payout → filter to the payout band, **sort by joint_prob desc**.
- Pin probability → filter to `joint_prob ≥ floor`, **sort by combined_odds desc**.
- Both pinned → filter on both, sort by joint_prob desc.
- Return top-N.

### 5.4 Combinatorial safety (fixes the nightly OOM)
Cap the candidate pool so that `C(N, max_legs)` stays bounded (target ≤ ~5M combos). With `max_legs=4` that means a tighter `N` than the old max-3 cap of 200 (`C(200,4)` ≈ 64.6M would OOM again). Keep the highest-`market_prob` legs when capping. The 0.55 floor already shrinks the pool substantially.

### 5.5 Persistence
- Write top-N to `parlay_recommendations` reusing the existing JSONB wrapper shape from `optimizer/team_parlay.py`: `{class: 'across_game', legs: [...]}`.
- **Drop the `ev` field.** No EV/edge claim is made by this builder.
- Per-leg JSONB carries `market_prob`, odds, side, label, and `model_prob` (context).

### 5.6 Reuse, don't duplicate
Import `american_to_decimal`, `devig`/`odds_to_probability`, and the same-game exclusion semantics. `optimizer/parlay.py` and `optimizer/team_parlay.py` stop being invoked by the daily chain but stay in-tree for their helpers and as the tested substrate for the same-game v2.

## 6. API (stage 1)

New read-only endpoint, additive only:

```
GET /parlay-builder?target_payout=&min_prob=&max_legs=&floor=&sport=mlb
→ [{ legs: [{game_id, kind, label, side, line, odds, market_prob, model_prob}],
      combined_odds, joint_prob, n_legs }]
```

Behind the existing global API-key dependency. **No `ev` field.** Does **not** modify `/edges`, `/parlay-recommendations`, `/game-predictions`, `/box-scores`, `/games` — the Budgerr contract (README §7.1) is additive-only and must stay intact.

## 7. Dashboard (stage 2)

New page in `web/`, matching `web/app/edges/` conventions and DESIGN.md (near-black terminal surface, one signal-green accent, Geist Sans/Mono). Read PRODUCT.md + DESIGN.md before building; see `web/AGENTS.md` for the Next 16 caveats.

- Two controls: **target payout** and **minimum joint probability** — pin either.
- Each result shows its **joint probability front and centre** ("≈ X% to hit") — this is the real risk and must be the most prominent number on the card.
- Per leg: `market_prob` (authoritative) and `model_prob` (muted, labelled "model — not used for ranking").
- Honest framing copy: what joint probability means, and that ~2x ≈ a coin flip.
- **No "+EV", "edge", "value", or "beat the market" language anywhere.**

## 8. Daily chain integration

In `scripts/daily_chain.sh`, replace the `optimizer.parlay` step with `python -m optimizer.builder` at the two default targets (~1.4x, ~2.0x), capped. `modeling.settle` already runs later in the chain and will score the new `parlay_recommendations` rows with no new settlement code. The existing dashboard "Betting record (paper)" section then surfaces the builder's real W-L-P / ROI.

This retires the step that OOM-died (SIGKILL) nightly and pushed a false failure alert every morning (§11 chain caveat).

## 9. Testing

Pure-math unit tests following `tests/test_parlay.py` (DB-free, runs under `env -i`, added to CI):
- Favorite-side selection from two-sided odds (including the underdog-side-preferred-by-model case).
- Floor filtering at 0.55.
- Two-axis filter: pin payout vs. pin probability, and both.
- `joint_prob` / `combined_odds` are exact products.
- Across-game exclusion (no combo shares a `game_id`).
- Candidate-pool cap actually bounds `C(N, max_legs)`.
- Player + team leg normalization into the common schema.
- One-sided-line skip.

## 10. Guardrails (do not violate)

1. Rank **only** on devigged market probability. Never on `model_prob`.
2. No "+EV" / "edge" / "value" / "beat the market" claims in UI, API payloads, or recommendation JSONB.
3. Always surface the joint probability prominently — it *is* the risk.
4. Favorite-side legs only, `market_prob ≥ floor`.
5. Across-game only (independent) in v1.
6. No real-money deployment. This is an honest constructor + paper-trading sandbox.

## 11. Future work (explicitly deferred, in priority order)

1. **Same-game combos.** Start with NRFI+F5 via the empirical lift already built and tested in `modeling/correlation.py` + `optimizer/team_parlay.py:same_game_pairs`, surfaced as a separate labelled class showing its **sample size**. Then player+team and player+player same-game correlation (§14.2: copulas over discrete marginals are non-unique; opus-grade, don't front-load). Only trustworthy with ~a season of shared history.
2. **Improve model resolution.** The builder deliberately avoids depending on the models, but improving them would let `model_prob` graduate from context to a real filter. Per §11 this needs *genuinely new predictive data* (park factors, weather, umpire, lineups, deep pitcher/bullpen stats), not tuning — making the model stronger made R² worse. Gated by §11's permanent acceptance test: `corr`/R² of predicted-vs-actual well above zero **and** `predicted_mean` tracking the book line with slope → 1.
3. **Line shopping / best-price legs** (§14.2). The builder ranks on consensus devigged prob; SGO already returns a `byBookmaker` breakdown that `odds_ingest.py` throws away. Using the best available price per leg strictly improves payout at fixed risk. Cheapest real improvement, zero modeling, zero extra quota.
4. **Kelly stake sizing** (§14.2): ¼-Kelly per parlay-as-one-bet plus a same-night total-exposure cap.

## 12. Substrate already in place

- `optimizer/parlay.py` — player builder (model-centric; superseded, helpers reused).
- `optimizer/team_parlay.py` — team NRFI/F5 builder incl. same-game pairs + JSONB wrapper (dormant; v2 substrate).
- `modeling/correlation.py` — empirical NRFI×F5 lift.
- `modeling/edges.py` — `devig()` / `odds_to_probability()` / latest-lines query pattern.
- `modeling/f5.py`, `modeling/team_edges.py`, team-aware `modeling/settle.py`.
- Migration `006_team_markets_f5.sql` applied live; `runs_f5` backfilled 3 seasons; `game_lines` captures F5 (`1ix5`) + NRFI (`1i`); `prop_lines` has player-prop lines.
- `parlay_recommendations` + `recommendation_outcomes` ledger; `scripts/daily_chain.sh` with heartbeat + missed-run self-heal.
- 143 pytest cases green.
