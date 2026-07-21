# Low-Risk Parlay Builder — Stage 2: Dashboard — DESIGN 2026-07-21

Stage 2 of the low-risk parlay builder (README §15). Stage 1 (engine, persistence,
settlement, `GET /parlay-builder`) is built and live; see §15.10. This spec covers
the dashboard page and the API changes it requires.

Every decision below is user-confirmed 2026-07-21. Do not relitigate them, and do
not relitigate the seven §15.3 decisions or the §15.8 guardrails they inherit.

## 1. Goal

A dashboard page that lets the user construct a low-risk parlay against tonight's
slate and see, honestly, how likely it is to hit. It is a constructor and a
paper-trading surface, not a betting product. It makes no claim of edge or value.

Inherited and binding (README §15.8):

1. Rank only on de-vigged market probability. Never on `model_prob`.
2. No "+EV" / "edge" / "value" / "beat the market" language in UI copy, component
   names, API payloads, comments, or commit messages.
3. Joint probability is always surfaced prominently. It *is* the risk.
4. Favorite-side legs only, `market_prob >= floor` (0.55).
5. Across-game legs only in v1.
6. No real-money deployment.

## 2. The two constraints that shaped this design

Neither appears in README §15.6, and both were measured on the 2026-07-21 slate.

**Search is slow.** A `/parlay-builder` request takes 4–13s end to end. Loading
legs is 0.38s of that; the search is 2–8s. Live-updating controls are therefore
impossible, and any design that searches on page load makes the page take 5–13s
to respond.

**Every search truncates.** On a 2443-leg / 19-game slate, `target_payout=2.0`,
`target_payout=1.4`, and `min_prob=0.75` all hit the 5,000,001-node budget and
return `truncated=True`. The CLI prints `WARNING: search hit its node budget —
results are partial, not exhaustive`, but the API drops it: `BuilderParlayOut`
has no field for it. A page built on this spec's honesty premise would otherwise
present partial results as complete. README §15.10 records truncation as a
2.0x-only problem; that is now stale and is corrected as part of this work.

## 3. Decisions (user-confirmed 2026-07-21)

| # | Decision | Choice |
|---|----------|--------|
| 1 | First paint | **Server-render the saved nightly constructions**; controls refine below with an explicit Build and a real pending state. No search on page load. |
| 2 | Truncation | **Surface it in the API and the UI** (this spec). Separately, **raise/tune the node budget** so common queries finish exhaustively — split into its own plan (see §7). |
| 3 | Controls | **Mode toggle**: "I want a payout of…" / "I want a hit chance of at least…". Only the pinned control is editable; the other is shown as the derived result. Both-pinned is not exposed in v1. |
| 4 | Paper record | **Compact record strip on the page**, `parlay_builder` only. |
| 5 | Signal color | **Green means "safe", not "edge"** — joint probability turns Signal Green at or above 75%, Ink below. Requires a one-line DESIGN.md amendment. |
| 6 | Route | **`/builder`**, titled "Parlay Builder". |
| 7 | Saved-row source | **A new builder-scoped read** (`/parlay-builder/saved`) of the precomputed nightly rows, returning a superset of the live-search shape. Now also a **finalized Budgerr-facing contract** (see §6.2). `/parlay-recommendations` stays out of the dashboard's path entirely. |

## 4. Prerequisite — LANDED 2026-07-21 (`38e9616`)

Three changes merged to main before Stage 2 starts; Stage 2 depends on all three.
All three are live and verified (195 tests green, checked against the running API).

1. **A pinned target payout becomes a floor**, not the centre of a tolerance band,
   with progressive widening so the ceiling prune (and therefore the node budget)
   is not lost. Ranking by joint probability inside a symmetric band always
   returned the band's bottom: `--target-payout 2.0 --tolerance 0.10` returned
   1.80x, and the API's default tolerance returned 1.73x for a 2.0 request.
2. **The paper ledger splits by kind.** `settle_builder_parlays` writes outcomes
   with `bet_type='parlay'`, pooling the builder with the legacy model-ranked
   parlays (16-48-0, −57% ROI). `/bet-performance` now reports `parlay_model`,
   `parlay_team`, `parlay_builder` separately, derived via the `parlay_id` join —
   no migration, the `bet_type` CHECK constraint is untouched.
3. **`/parlay-recommendations` filters to `kind IN ('player','team')`.** Without
   it, that endpoint 500s the first morning the builder runs alone, taking down
   Budgerr's Tonight view. See §8.

The node-budget engine work (§7) is a **separate plan**, sequenced *after* the
merged payout-floor change (1), because both rewrite the same search bounds in
`builder_core.build()`. It is independent of the dashboard plan — the dashboard
surfaces whatever the `truncated` flag reports regardless of how far the budget
is later tuned — so the two plans can proceed in either order or in parallel.

## 5. Architecture

```
web/app/builder/page.tsx             server component — fetch, layout, error state
web/app/builder/BuilderControls.tsx  client — mode toggle, active control, Build
web/app/builder/ConstructionList.tsx shared renderer for saved AND searched results
web/app/builder/RetryButton.tsx      matches web/app/edges/ and web/app/clv/
web/app/builder/builder.module.css
```

`ConstructionList` renders both the saved nightly rows and fresh search results.
This is the load-bearing structural choice: a saved construction and a searched
one must be visually identical, because they are the same object. It takes
`BuilderParlayOut[]` and knows nothing about where they came from.

`BuilderControls` owns all client state (mode, pinned value, pending, results,
error). `page.tsx` stays a server component and passes the saved constructions in
as the initial value.

**Data flow.** `page.tsx` fetches in parallel: the saved builder constructions and
`/bet-performance`. Both go through `web/app/lib/api.ts`, which holds the API key
server-side. On failure the page shows the existing error state with `RetryButton`,
matching `web/app/edges/page.tsx`.

A Build press must reach the API through a **server-side path** — a server action
or a route handler under `web/app/api/`. The API key must never reach the client.
The implementing agent chooses between the two after reading
`web/node_modules/next/dist/docs/`; this is Next 16.2.10 and `web/AGENTS.md`
warns that its conventions differ from training data (`proxy.ts`, not
`middleware.ts`). Do not guess the idiom.

## 6. API changes

Neither change touches an existing Budgerr contract endpoint (`/edges`,
`/box-scores`, `/games`, `/game-predictions`, `/parlay-recommendations`). §6.1
changes a Stage-1 endpoint that has no external consumer yet; §6.2 *creates* a
new endpoint that is deliberately promoted to a Budgerr contract surface (see
§6.2). "Additive-only" therefore applies going forward to §6.2 the moment it
ships, and to the existing §7.1 surfaces throughout.

**6.1 `/parlay-builder` returns an object, not a bare list.**

```
GET /parlay-builder?target_payout=&min_prob=&max_legs=&floor=&top_n=
→ { constructions: [ BuilderParlayOut, ... ],
    truncated: bool,
    nodes_searched: int,
    exhaustive: bool }
```

Truncation is a property of the search, not of any one construction, so the bare
list has nowhere to put it. This endpoint is new in Stage 1 and the dashboard is
its only consumer, so the shape change is free now and gets strictly more
expensive with every consumer added. `BuilderParlayOut` itself is unchanged.

**6.2 `GET /parlay-builder/saved?limit=` — a builder-scoped read of the
precomputed nightly constructions.** It reads `parlay_recommendations` where
`kind='builder'`, most recent first, unwrapping the
`{"class": "across_game", "legs": [...]}` JSONB wrapper.

It returns a superset of `BuilderParlayOut` — the same `legs`, `combined_odds`,
`joint_prob`, `n_legs` core so `ConstructionList` renders saved and searched
results with one component, plus three saved-only fields the dashboard ignores
but an external consumer needs: `parlay_id` (identity), `created_at` (slate
freshness), and `target_payout` (which nightly target — 1.4x or 2.0x — produced
it). Define `SavedBuilderParlayOut(BuilderParlayOut)` adding those three; do not
mutate `BuilderParlayOut` itself.

**This endpoint is a finalized external contract (user-confirmed 2026-07-21),
Budgerr-facing, additive-only** — the same discipline as the §7.1 surfaces. The
Budgerr session independently measured the live `/parlay-builder` search at
~8–13s through its 10s proxy (502s on timeout), corroborating §2, and asked for
a precomputed listable "low-risk parlay of the day" rather than a live call.
This endpoint is exactly that: a fast list read (~0.3s, like
`/parlay-recommendations`), off Budgerr's critical path. Consequences for the
build:

- **Field names and shape are now a contract.** Once shipped, changes are
  additive only. Name the fields as above and do not rename them later.
- **Team legs carry no team identity in `label`.** NRFI/F5 are game-level
  markets: a team leg is `kind:"team"`, `player_id:null`, `stat_type:null`,
  `market ∈ {"first_inning_runs","f5_runs"}`, and `label` is just
  `"first_inning_runs under 0.5"`. The matchup is resolved via `game_id` →
  `/games`. This is a known shape, not a gap to fix here, but it must be
  documented in the endpoint's docstring so the consumer knows to join on
  `game_id`.
- A short README note (new subsection under §7.1 or §15) must record that
  `/parlay-builder/saved` is a Budgerr contract surface, so a future session
  doesn't treat it as a private dashboard read and break it.

Neither change may touch `/edges`, `/box-scores`, `/games`, `/game-predictions`,
or `/parlay-recommendations`. Note also that §6.1 changes the *live*
`/parlay-builder` response from a bare list to an object — Budgerr probed the
current bare-list shape, so this change must be communicated to that session
before they build against it.

## 7. Engine work — raise the node budget (SEPARATE PLAN)

Split out of this dashboard spec at the user's direction (2026-07-21). It is
tracked as its own plan,
[`docs/superpowers/plans/2026-07-21-builder-node-budget.md`](plans/2026-07-21-builder-node-budget.md),
and does not block the dashboard: the dashboard honestly surfaces whatever the
`truncated` flag reports (§6.1), so the two can ship in either order.

Summary of its intent (full detail in that plan): make the common dashboard
queries finish exhaustively rather than truncating. Tighten exact pruning before
raising `MAX_NODES`, since a raised budget trades latency directly against the
4–13s response time. The suffix-maximum bound and price-ascending within-game
ordering are exact; any heuristic that could drop the true optimum is out of
scope — Stage 1's search is exact and must stay exact. If a query still truncates
after tuning, that is acceptable and surfaced in the UI, never hidden by raising
the budget until the warning stops appearing.

## 8. Why `/parlay-recommendations` is fenced off

Recorded here because it is the kind of thing that recurs.

`list_parlay_recommendations` selects the N most recent rows with no `kind`
filter. Builder rows store legs as a dict wrapper; psycopg2 returns JSONB already
parsed, so `json.loads(dict)` raises `TypeError` — the same bug fixed in
`modeling/settle.py` and never fixed here. Team legs would then `KeyError` on
`player_id`.

Measured against the running service on 2026-07-21: `limit=10` returned 200,
`limit=20` returned 500. The endpoint survived only because the legacy chain step
had written newer rows minutes earlier. From the first morning the builder runs
alone, the ten newest rows are all `kind='builder'` and the default `limit=10`
fails.

The fix filters to `kind IN ('player','team')`, restoring the pre-builder
contract exactly. The builder has its own endpoint with a schema designed for
mixed player and team legs. **Do not remove that filter.**

## 9. Page composition

**Header.** "Parlay Builder", with a back link matching `web/app/edges/page.tsx`.

**Record strip.** `parlay_builder` W-L-P, ROI, and n from `/bet-performance`.
Until the first builder slate settles, `/bet-performance` has **no
`parlay_builder` row at all** (verified live 2026-07-21 — the endpoint returns
`edge`, `parlay_model`, `all`, and no builder key, because zero builder outcomes
exist). The page must treat a missing `parlay_builder` key as 0-0-0 / n=0 and say
so in words, not render an empty table and not error on the absent key. This is
the normal state for the first day or two, and it is honest.

**Framing copy.** Permanent, not dismissible, not behind a disclosure. Covers:
what joint probability means; that a ~2x parlay is roughly a coin flip; that each
additional leg adds another vig bite, so at a fixed payout fewer legs is strictly
safer; that ranking is on the book's de-vigged price rather than our model; and
that this is paper only. Body role, capped at 65–75ch per DESIGN.md.

**Controls.** A two-way mode toggle. Only the pinned control is editable; the
other axis is presented as a result, not an input. The toggle labels are prose
("I want a payout of…" / "I want a hit chance of at least…") so the duality of
the two axes is legible without knowing the API. Build is the page's one primary
action.

**Constructions.** Per card:

- Joint probability is the largest element on the page: `≈ 74.4% to hit`, Geist
  Mono with tabular figures, Display scale. **Signal Green at >= 75%, Ink below.**
- Payout (`1.26x`) and leg count beside it, Data role.
- Per leg: player or market label and side in Geist Sans; line, odds, and
  `market_prob` in mono Ink — `market_prob` is authoritative. `model_prob` in
  Muted, labelled "model — not used for ranking", rendered as `—` when null
  (common: many live legs have no matching `edges` row).

**Truncation notice.** When `truncated` is true, an Edge Amber note stating the
results are the best found within the search budget and are not proven optimal.
Edge Amber is DESIGN.md's "worth a second look" role, which fits exactly, and it
stays visually distinct from the green. Never render Edge Amber and Signal Green
inside the same element.

## 10. States

| State | Treatment |
|---|---|
| Pending | Build disabled, explicit copy owning the wait: "Searching constructions — this takes a few seconds." A spinner alone is not enough for 5–13s. |
| No results | Explain the cause — the pinned floor is unreachable on the available slate — and suggest relaxing it. Not a bare "no results". |
| API unreachable | Existing error state plus `RetryButton`, matching `web/app/edges/page.tsx`. |
| Truncated | §9's amber note, alongside the results. |
| Empty saved rows | Before the first nightly builder run, say the nightly constructions have not been recorded yet. |

## 11. Design system

DESIGN.md's One Signal Rule reserves Signal Green for "a real, calibration-checked
edge", which this page is forbidden to claim. The rule is broadened to "the one
number you came for", and on `/builder` that number is joint probability. This
requires a one-line amendment to DESIGN.md §2 recording the broadened rule and
its application here. Everything else is unchanged: near-black surface, tonal
ladder rather than shadows, Geist Mono with tabular figures for every number,
Geist Sans for chrome and prose, 6px radii, no accent stripes, no KPI tiles.

## 12. Testing and verification

`web/` has no test infrastructure (no test runner, no lint script in
`package.json`) and none is added here — that would be scope creep.

- **API changes**: pytest cases following existing `tests/` conventions, covering
  the new response object, the builder-scoped read including the dict-wrapper
  unwrap, and the truncation flag being set when the budget is hit.
- **Engine changes**: pure-math tests in `tests/test_builder_core.py`, DB-free,
  runnable under `env -i`. Exactness must be preserved and asserted.
- **Frontend**: `npm run build` in `web/` must pass (this is the typecheck), then
  the page is driven in a real browser — logged in, against the live API — to
  confirm first paint without a search, that Build produces results with a visible
  pending state, that the truncation notice appears when the flag is set, and that
  the record strip renders. Screenshots as proof.
- **Regression**: `/edges` and `/clv` must still render, and
  `/parlay-recommendations` must return 200 at `limit=10` and `limit=50`.

## 13. Out of scope

Same-game legs (README §15.9.1), line shopping (§15.9.3), Kelly staking
(§15.9.4), any model-probability-based ranking or filtering, both-axes-pinned in
the UI, exporting or placing bets, and any change to the Budgerr contract
endpoints.
