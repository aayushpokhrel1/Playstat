# Same-Game Combos (NRFI + F5) — Design Spec

**Date:** 2026-08-07
**README earmark:** §15.9 item 1 (first future-work priority)
**Status:** design approved (user, 2026-08-07); pending plan.

## 1. Purpose

The low-risk builder deliberately constructs **across-game only** parlays (guardrail
§15.8 #5), because it treats every leg as independent and multiplies probabilities.
Some *same-game* team bets are **not** independent — they are positively correlated.
The canonical MLB pair is **NRFI** (no run in the 1st inning, `first_inning_runs`
under 0.5) and **F5-under** (few runs through 5 innings, `f5_runs` under its line):
in a low-scoring game both tend to hit together, so the naive product understates
the true joint hit-rate.

This adds a separate, **clearly-labelled** parlay class — the deliberate exception
to the across-game-only guardrail — that surfaces one NRFI+F5 same-game card per
eligible game, correcting the joint probability by an **empirically-measured
correlation lift**, and being honest about three things the naive product hides:
(a) the real chance both legs hit, (b) that the product payout is **not placeable**
as a real same-game parlay, and (c) how much shared history the correlation is
based on.

**Scope of v1:** NRFI + F5 only (the two MLB team markets). Player+team and
player+player same-game correlation (copulas over discrete marginals — non-unique,
opus-grade) is explicitly a **later phase**, not front-loaded (§14.2 / §15.9 item 1).

## 2. Grounding (measured live 2026-08-07, real data)

`team_game_stats` holds `runs_inning_1` and `runs_f5` for **6,588 games** (all
history pooled) — the lift is well-powered today.

Global observed lift = `P(both) / (P(a)·P(b))`, by favorite-side combo, at the
**real** market lines (fi 0.5, f5 4.5/5.5):

| sides (NRFI/F5) | lift @ f5 4.5 | both_n | lift @ f5 5.5 | both_n |
|---|---|---|---|---|
| under/under | **1.30** | 2197 | **1.22** | 2544 |
| over/over   | **1.32** | 2111 | **1.38** | 1697 |
| under/over  | 0.70 | 1162 | 0.64 | 815 |
| over/under  | 0.69 | 1118 | 0.77 | 1532 |

Same-direction pairs land ~30% **more** often than independence assumes;
opposite-direction ~30% **less**. Every joint cell is ≫ 50 — MLB gating passes clean.

**Slate reality:** on the 2026-08-07 MLB slate, 14 NRFI + 14 F5 lines exist but only
**1 team leg clears the 0.55 favorite floor**, and **0 games** have both NRFI and F5
clearing it. So the class is **empty most nights** — expected, exactly like the team
tier (§15.10), not a bug.

## 3. Design judgements (all user-confirmed 2026-08-07)

1. **Lift measurement — per actual line + side.** Measure the lift at the pair's
   *real* market lines (fi 0.5, that game's f5 line) and the two *favorite* sides,
   global across all history. Not the dormant code's hardcoded `nrfi_line=1.5,
   f5_line=4.5` (which measures the wrong event — the market NRFI line is 0.5).

2. **Payout honesty — show product, labelled non-placeable.** Lead with the
   lift-adjusted **true joint** (the risk, guardrail #3). Show `combined_odds` =
   product of the two shopped prices as a **reference ceiling**, captioned:
   *"independent-pricing reference — a book same-game parlay is repriced shorter or
   restricted."* No +EV / edge / value / green language (guardrail #2).

3. **Sample gating — games + joint cell.** SHOW a card only if `n_games ≥ 500 AND
   both_n ≥ 50`; attach a prominent **small-sample warning** while `n_games < 2000`
   (~under one MLB season of shared history). MLB passes clean today (no warning);
   a cold-start sport stays hidden until it accumulates. Rationale for the joint-cell
   floor: a degenerate side/line combo with few co-occurrences would otherwise
   surface a wild, noisy lift.

4. **Code layout — in `builder.py` + `builder_core.py`.** Add a pure
   `same_game_pairs()` to `builder_core.py` (DB-free, fake-engine testable), driven
   by a new `--same-game` mode on `optimizer/builder.py` that **reuses**
   `builder.load_team_legs` (game_lines, devig favorite, 0.55 floor, line-shopping,
   slate window, `--sport`). Retire `optimizer/team_parlay.py`'s dormant
   `game_edges` code. Keep `modeling/correlation.py` (fix its lift fn).

## 4. Components

### 4.1 `modeling/correlation.py` (fix the lift fn)
- Keep pure `empirical_lift(both, a, b, n)` and `pair_joint_prob(p_a, p_b, lift)`
  **unchanged** — they are correct.
- **Change `nrfi_f5_lift`** signature to take the pair's actual lines and return the
  sample cells:
  ```
  nrfi_f5_lift(engine, side_nrfi, side_f5, nrfi_line, f5_line) -> (lift, n_games, both_n)
  ```
  It reads `team_game_stats` (`runs_inning_1`, `runs_f5`, summed per game across both
  teams), computes the marginal hits at the given sides/lines, and returns the lift,
  the number of games underlying it, and the joint "both hit" cell count. Thin DB
  wrapper; the arithmetic stays in the pure `empirical_lift`.

### 4.2 `optimizer/builder_core.py` (new pure `same_game_pairs`)
```
same_game_pairs(team_legs, lift_fn, top_n) -> list[construction]
```
- `team_legs` are already-normalized, floor-passing builder team legs (from
  `builder.load_team_legs`): each has `game_id`, `market`, `side`, `market_prob`,
  `decimal_odds`, `american_odds`, `line_value`, `label`, `book`, `kind="team"`.
- Group by `game_id`. For each game with **both** a `first_inning_runs` leg and an
  `f5_runs` leg (both already clear the 0.55 floor), emit **one** construction:
  - `joint_prob` = `pair_joint_prob(p_nrfi, p_f5, lift)` (lift-adjusted true joint).
  - `combined_odds` = `nrfi.decimal_odds * f5.decimal_odds` (product; reference).
  - `lift`, `lift_n` (= n_games), `both_n`, and the two favorite sides carried on
    the construction.
  - `legs` = `[nrfi, f5]`.
- `lift_fn(side_nrfi, side_f5, nrfi_line, f5_line) -> (lift, n_games, both_n)` is
  injected (the builder wires it to `correlation.nrfi_f5_lift` with a cache), so this
  function stays **DB-free and unit-testable**.
- No `--target-payout` floor: the qualifying set is naturally ≤ one card per game.
  Rank the emitted cards by lift-adjusted `joint_prob` descending, return top-N.
  (Diversity/dedup are moot — each game appears at most once.)

### 4.3 `optimizer/builder.py` (`--same-game` mode)
- New `--same-game` flag (mutually exclusive-ish with `--team-only`; if both, error).
- Loads legs via the existing `load_team_legs(engine, floor, slate_date, sport,
  window_days)` — **the same guardrail-correct path the live builder uses** — so the
  dormant `game_edges` query is never touched.
- Builds a cached `lift_fn` closure over `correlation.nrfi_f5_lift(engine, ...)`.
- Calls `same_game_pairs(...)`, applies the **sample gate** (drop a card whose
  `lift_n < 500` or `both_n < 50`), and `--save` persists with
  `parlay_class="same_game_pair"`.
- MLB-only in practice (NRFI/F5 are MLB team markets; `TEAM_MARKETS["mlb"]`).

### 4.4 `save_builds` (carry lift metadata)
- Extend the persisted JSONB wrapper for same-game rows to carry the correlation
  metadata alongside `class`/`sport`/`legs`:
  ```json
  {"class": "same_game_pair", "sport": "mlb", "lift": 1.30, "lift_n": 2197,
   "both_n": 2197, "small_sample": false, "legs": [...]}
  ```
  (`both_n` is the joint cell; `small_sample` = `lift_n < 2000`.) Additive keys —
  player/team/game rows and Budgerr's player-tier consumption are byte-unchanged.
  Implementation: `save_builds` gains an optional `extra: dict | None` merged into
  the wrapper (default `None` → today's exact behaviour for all other classes).

### 4.5 API — `/parlay-builder/saved` tier plumbing (additive)
- `TIER_TO_CLASS`: add `"same_game": "same_game_pair"`.
- `_CLASS_TO_TIER`: add `"same_game_pair": "same_game"`.
- `_TIER_SORT_ORDER`: add `"same_game": 3` (sorts after player/team/game).
- `SavedBuilderParlayOut`: add **additive optional** fields `lift`, `lift_n`,
  `both_n`, `small_sample` (default `None`/`False`), populated from the wrapper for
  same-game rows, `None` for all others. Budgerr requests `tier=player` (default),
  which is byte-unchanged; the new fields are absent/None there.

### 4.6 Dashboard — "Same-game combos" section
- A new labelled section on the builder page (mirrors the team-tier section pattern),
  fed by `/parlay-builder/saved?tier=same_game&sport=mlb`, slate-scoped like the
  others.
- Renders per card: the two legs (NRFI + F5, with existing team-name/matchup
  rendering), the **lift-adjusted joint prominently** (the risk), the correlation
  lift + `n` ("based on N games"), and the two captions:
  1. non-placeable payout caption (§3.2);
  2. small-sample warning **only when** `small_sample` is true.
- Honest empty state ("No same-game combos tonight") — expected most nights.
- Monochrome, no signal-green (reserved for the ≥75% joint-prob rule), no edge/EV.

### 4.7 Chain — one nightly MLB `--same-game` step
- Add a single `optimizer.builder --same-game --save` MLB step to
  `scripts/daily_chain.sh`, best-effort / non-fatal (like the NBA builds), placed
  with the other pre-game builder saves (ahead of settle). Not API-imported → no
  kickstart for the chain change (but `builder.py`/`builder_core.py` **are**
  API-imported, so the code change requires a kickstart — architect lane).

## 5. Settlement, record, staking (no scoring changes)

- **Settlement unchanged.** A same-game card is two `kind="team"` legs on one
  `game_id`; `settle_builder_parlays` settles each leg via the existing
  `game_total()` path (`runs_inning_1` / `runs_f5`) and ANDs them. No distinct-game
  assumption exists in that path. (Verified read of `settle_builder_parlays` /
  `parlay_result` / `game_total`; the plan re-confirms live.)
- **Record — own bucket, doubles as the lift's empirical check.** The per-`(class,
  target_payout)` record split (§15.10) already isolates `same_game_pair` into its
  own row via `_CLASS_TO_TIER`. Tracking it tells us whether the measured lift holds
  up in reality (does under/under really land ~39%?). Its ROI is computed at the
  non-placeable product odds and carries the same "reference" caption.
- **Kelly stake flows through as-is** (the stake pass sizes any card by
  `joint_prob` × `combined_odds`); the whole same-game card — payout *and* stake — is
  captioned paper-reference.

## 6. Guardrails honored (§15.8)

1. Rank on **devig market_prob** — `p_a`, `p_b` are the two favorite sides' devig
   probs; the lift is an empirical **dependence** measured from box-score outcomes
   (not a model), applied to market marginals. Zero `model_prob`.
2. No +EV / edge / value / beat-the-market language anywhere.
3. Joint probability surfaced prominently — it *is* the risk (lift-adjusted here).
4. Favorite-side legs only, each `market_prob ≥ 0.55`.
5. Across-game-only is the v1 default; **this class is the deliberate labelled
   exception** (architect-authorized, §15.8/§16).
6. Paper-only; no real-money deployment. Honest constructor.

**Additive-only + mlb-default:** `/parlay-builder/saved` (player tier),
`/box-scores`, `/games` response shapes are byte-unchanged; every new field/param is
optional/defaulted. Budgerr-safe.

## 7. Testing

Pure / fake-engine only (no live DB — `ingestion.db.get_engine()` is LIVE):
- `empirical_lift` / `pair_joint_prob` edge cases (empty marginals → 1.0; clamp to
  `[0, min(p_a,p_b)]`; positive & negative lift directions).
- `same_game_pairs`: emits one card per game with both markets; skips a game missing
  either; ranks by lift-adjusted joint; top-N; carries lift/lift_n/both_n; handles
  the empty-legs and no-eligible-game cases.
- Sample gate: card dropped when `lift_n < 500` or `both_n < 50`; `small_sample`
  flagged when `lift_n < 2000`.
- `save_builds` `extra`-wrapper: same-game row carries lift keys; other classes
  byte-unchanged (fake-engine capture, `tests/test_builder.py:_CapturingEngine`).
- API tier mapping: `tier=same_game` filters `class='same_game_pair'`; `tier=player`
  byte-unchanged; `SavedBuilderParlayOut` new fields None for non-same-game rows.
- `nrfi_f5_lift` with a fake engine returning a small fixed frame (verify it reads
  the given lines/sides and returns `(lift, n, both_n)`).

## 8. Rollout (architect reserved lanes)

1. Land spec + plan (commit).
2. Delegate bulk edits on a clean tree; review the **actual diff**.
3. Run pytest (architect).
4. Commit; **kickstart** the API (`builder.py`/`builder_core.py`/`main.py` are
   API-imported).
5. Live verify: `/parlay-builder/saved?tier=same_game` returns 200 (empty is fine),
   `tier=player` byte-unchanged; a forced same-game build on a synthetic-eligible
   game (read-only / reverted write) shows lift + captions; browser section renders.
6. Update README §15.9 item 1 in the same commit; push.

**No DB migration** — the change is entirely additive JSONB wrapper keys +
optional API fields.
