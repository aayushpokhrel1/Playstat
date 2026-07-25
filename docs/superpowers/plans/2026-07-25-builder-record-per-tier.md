# Builder record — per-tier / per-target breakdown

Spec-complete. The dashboard's builder betting record currently shows ONE pooled
number (e.g. "19-1-1 · ROI 24.6%"), which misleadingly lumps together the 1.4x
(~67%-to-hit) and 2.0x (~50%-to-hit) player parlays and the team tier — very
different bets. Show each tier/target as its own W-L-P record instead.

## Approach (LOCKED)

Add a NEW dashboard-only endpoint — do NOT modify `/bet-performance` (it also
feeds `web/app/clv/page.tsx`, and we don't want to disturb that or any external
reader). The breakdown is the same settled-builder data grouped by tier + target.

## API — `api/main.py`

`GET /parlay-builder/record` → `list[BuilderRecordOut]`, one row per
`(legs->>'class', target_payout)` over settled builder parlays:

```sql
SELECT pr.legs->>'class' AS cls, pr.target_payout,
       count(*) AS n,
       sum((ro.result='win')::int)  AS wins,
       sum((ro.result='loss')::int) AS losses,
       sum((ro.result='push')::int) AS pushes,
       sum(ro.pnl) AS pnl
FROM recommendation_outcomes ro
JOIN parlay_recommendations pr ON pr.parlay_id = ro.parlay_id
WHERE pr.kind = 'builder'
GROUP BY 1, 2
ORDER BY 1, 2
```

`BuilderRecordOut` (new Pydantic model in `api/schemas.py`):
`tier: str` ("player" for `across_game`, "team" for `team_tier`),
`target_payout: float`, `n: int`, `wins: int`, `losses: int`, `pushes: int`,
`pnl: float`, `roi: float` (= `pnl / n`, stake is 1u/parlay; 0.0 when n==0).
Map `cls`→`tier` and compute `roi` in the handler. Order rows player-before-team,
then ascending `target_payout`. Empty result (no settled builder parlays) → `[]`.

Factor the row→`BuilderRecordOut` shaping (cls→tier label, roi calc) into a small
PURE helper so it is unit-testable without a DB.

## Dashboard — `web/app/builder/`

READ PRODUCT.md, DESIGN.md, web/AGENTS.md (Next 16) first; match existing
`web/app/builder/` + `web/app/edges/` conventions. In `web/app/lib/api.ts` add
`getBuilderRecord()` → `apiGet<BuilderRecordOut[]>("/parlay-builder/record")`
(server-side, key stays server-side, same pattern as `getSavedBuilderParlays`).

Replace the single pooled record line in the "Betting record (paper)" region of
`page.tsx` with per-tier/per-target rows, e.g.:
```
Player 1.4x   10-0-0   +2.81u   ROI +28%
Player 2.0x    9-1-1   +3.27u   ROI +30%
Team          — no settled team parlays yet —
```
- Mono/tabular figures for the numbers (DESIGN.md Tabular Figures Rule).
- Keep it muted/secondary — this is a record readout, not the headline (the
  green ≥75% joint-prob rule is unchanged and unrelated here; don't add green).
- Show a graceful empty state per tier that has 0 settled rows (team is often 0).
- If the endpoint returns `[]` entirely (no settled builder parlays yet), show
  the existing "record starts once tonight's slate finishes" style message.
- Keep a small "paper" caption; do not imply these are real bets.

## Tests (`tests/`)

CRITICAL SAFETY: NO test DB; `ingestion.db.get_engine()` is LIVE. New tests MUST
be pure or use the same isolation `tests/test_parlay_recommendations_api.py`
uses (inspect it) — never write the live DB, never call the endpoint against
`get_engine()` with a write path.
- Pure test of the row→`BuilderRecordOut` helper: `across_game`→"player",
  `team_tier`→"team", roi = pnl/n, roi 0.0 when n==0, ordering.
- Endpoint test via the API test's isolation: given fixture rows, returns the
  grouped shape; empty → `[]`.
- Full suite stays green (currently 234).

## Out of scope (architect does these)
- `/bet-performance` is unchanged — do not touch it or `web/app/clv/page.tsx`.
- launchd, `:8000` kickstart, git push, live DB writes. Work in the worktree,
  commit there only. The architect reviews, kickstarts the API, and browser-verifies.
