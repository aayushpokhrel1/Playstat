# Builder Stage 3 — slate window, DNP voids, dedicated team tier

Spec-complete implementation plan. All design decisions are LOCKED (user-confirmed
2026-07-23). The implementer makes no design calls — only the changes below.

Context to read first: README §15 (whole builder design), §15.8 (BINDING guardrails —
do not violate), §15.10 (KNOWN ISSUE + first settled record), §15.9 items 5 & 6.
The `market_prob >= 0.55` floor, "rank on devigged MARKET prob never model_prob",
"across-game legs only", "2–4 legs", and "paper only" guardrails are BINDING.

## Locked decisions

1. **Slate window = today's slate.** Candidate games restricted to `games.date =
   <slate_date>` (default `CURRENT_DATE`), still `AND status != 'FT'`. Excludes the
   futures prop lines that leaked Aug/Sep games into 07-21 parlays.
2. **DNP = void the leg.** When a leg's game is `FT` but there is no stat row for the
   player (scratched/DNP), void the leg (drop it like a push, settle on the remaining
   legs; all-void → no-action push). Standard book rule.
3. **Team markets = dedicated team tier.** A SEPARATE team-only parlay class, same
   0.55 floor, same exact search restricted to NRFI/F5 legs, honest joint-prob
   (green only ≥75%, so most read non-green), labelled higher-variance / may be empty.

## Change 1 — slate window (`optimizer/builder.py`)

- `load_player_legs` and `load_team_legs`: add a `slate_date=None` param (default →
  `CURRENT_DATE` in SQL). Add `AND g.date = :slate_date` to each JOIN on `games g`
  (keep the existing `AND g.status != 'FT'`). Thread `slate_date` through `load_legs`.
- `main()`: add `--slate-date` (default None → today) so the chain/API stay unchanged
  by default. The API route `parlay_builder` (api/main.py) calls `builder.load_legs` —
  it inherits the today default automatically; no API signature change required.
- VERIFY on real data (spare port / read-only): on today's slate `load_legs` returns
  only games with `date = today`; no Aug/Sep game_ids appear. Note the DB-timezone
  caveat (CURRENT_DATE is server-tz); confirm today's real games are selected.

## Change 2 — DNP voids (`modeling/settle.py`)

- In `settle_builder_parlays` (loop ~L414): the game-`FT` check already precedes the
  `actual is None` check, so if `actual is None` here the game IS final → the player
  did not play. Instead of `ready = False; break`, append result `"void"` for that
  leg (with its odds and an audit entry `{"result":"void","dnp":true,...}`) and
  CONTINUE. Do NOT require `line_value` for a void leg.
- `parlay_result` already drops anything that is not `"hit"`/`"miss"`, and all-dropped
  → `"push"`. So `"void"` needs NO change there — verify this in a test. (A void leg is
  neither hit nor miss → dropped; a parlay of [hit, void] settles as the single hit
  leg's odds; [void, void] → no-action push, pnl 0.)
- Apply the SAME rule to the shared `settle_parlays` (player path) and
  `settle_team_parlays` where they have the analogous `actual is None → not ready`
  branch, so the fix is consistent across all three (README §15.10 notes they share
  the shape). For team markets a FT game normally has a stat; treat a missing one as a
  void too (no data → void, not eternal-pending).
- Keep the game-not-`FT` branch as-is (still legitimately "not ready").

## Change 3 — dedicated team tier

- `optimizer/builder.py`:
  - Add a `--team-only` flag. When set, `main()` loads ONLY `load_team_legs` (no player
    legs) and runs the SAME `build()` with the given target(s). Print the same summary.
  - `save_builds`: add a `parlay_class="across_game"` param. Team-only saves pass
    `parlay_class="team_tier"`, written into the legs blob `{"class": <parlay_class>, ...}`.
- `scripts/daily_chain.sh`: after the two player builds (lines 94–95), add two team
  builds: `--team-only --target-payout 1.4 --tolerance 0.10 --top-n 5 --save` and the
  same at 2.0. Non-fatal if it finds nothing (the tier may be empty — that's expected;
  ensure the step exits 0 even with zero constructions so the chain doesn't fail).
- `api/main.py` `GET /parlay-builder/saved`: add optional `tier: str = "player"` query
  param. Map `player → legs->>'class' = 'across_game'` (the CURRENT default — preserves
  the exact existing Budgerr response), `team → 'team_tier'`, `all → no class filter`.
  Add `AND legs->>'class' = :cls` to the WHERE (skip when `all`). This is ADDITIVE:
  callers passing no `tier` get today's behavior unchanged. Do NOT alter
  `SavedBuilderParlayOut` fields (Budgerr contract, additive-only).
- Dashboard (`web/app/builder/`): add a second section "Team-market parlays" below
  the player tier, fetching `tier=team`. Copy: a muted note that team markets price
  near coin-flip so these are higher-variance and the tier may be empty. Same green
  rule (≥75% joint prob), same muted `model_prob` "(not used for ranking)". READ
  PRODUCT.md, DESIGN.md, web/AGENTS.md (Next 16: proxy.ts not middleware.ts) first;
  match `web/app/edges/` conventions; the builder page already has a server route
  keeping the API key server-side — reuse that pattern (do not expose the key client-side).

## Tests (`tests/`)

- DNP void: extend `tests/test_settle_builder.py` (it already seeds a DB) with a parlay
  whose player has a prop line but NO `player_game_stats` row on an FT game → the leg
  voids and the parlay settles on the remaining leg(s); an all-void parlay → push, pnl 0.
- `parlay_result` with a `"void"` in the results list behaves as a dropped leg (pure unit
  test, no DB).
- Team tier: `build()` on team-only legs returns team parlays; `save_builds(..., parlay_class="team_tier")`
  writes `class="team_tier"`; the saved endpoint with `tier=team` returns only those and
  `tier=player` excludes them (mirror `tests/test_parlay_recommendations_api.py` patterns).
- Slate window: if a DB fixture harness exists, assert future-dated games are excluded;
  otherwise add a focused test on the query/param and document the manual real-data check.
- Full suite must stay green (currently 201). Add, don't break.

## Out of scope (architect does these, NOT the implementer)

- Live regeneration / re-running `modeling.settle` against the live DB (clears the
  DNP-only stuck rows 88, 89; futures-mixed 07-21 rows settle naturally in Sept).
- `launchctl kickstart` of the live API after merge (imported-module change).
- Notifying Budgerr that `tier=team` is available.
- Any migration or live write. Implementer works in a worktree, commits there only,
  never touches `:8000`, launchd, or `git push`.

## Acceptance

- Full suite green (≥201). New tests cover DNP-void, team-tier save/fetch, void in
  parlay_result. On a spare uvicorn port (never :8000): `/parlay-builder/saved` default
  == unchanged player rows; `tier=team` returns team-tier rows; live `/parlay-builder`
  builds only today's games (no future game_ids). Dashboard renders both tiers per DESIGN.md.
