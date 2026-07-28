# Team names / matchup on builder parlay legs

Spec-complete (user decisions locked 2026-07-28). Builder parlay legs currently
render only `leg.label` — player legs show "Andrew Benintendi batter_strikeouts
over 0.5" (no team), team-market legs show "first_inning_runs under 0.5" (no team
at all). The user wants team context on every leg so player props are easy to
find in a sportsbook and team-market (NRFI/F5) legs say which game they belong to.

## User decisions (LOCKED)
- **Player legs:** show the full matchup with the **player's own team emphasized
  and first**, e.g. `**Reds** vs Athletics` above the existing label line.
- **Team-market legs** (NRFI/F5, `kind='team'`): these are game-level, so "which
  team" = the **matchup**, plain `Athletics @ Reds`.
- **Name format:** **nickname only** (derived), e.g. "Athletics", "Red Sox".

## Data model (verified)
- `teams` has full `name` only (e.g. "Oakland Athletics") — **no abbreviation
  column**, so nicknames are derived.
- Every leg blob carries `game_id`; player legs also carry `player_id`.
- `players.player_id -> team_id`; `games.game_id -> home_team_id, away_team_id`;
  `teams.team_id -> name`. All resolvable via batched joins.
- CAVEAT 1 — **naive nickname splitting is wrong**: last-token gives "Sox" for
  BOTH "Boston Red Sox" and "Chicago White Sox", and "Blue Jays"→"Jays" etc. Use a
  **static full-name→nickname map for all 30 MLB teams**; fallback to the full name
  for anything unmapped (never wrong, just longer).
- CAVEAT 2 — **`players.team_id` is a "latest pull"** (README §15.10 / NBA note):
  a traded player's stored team can differ from the team they played for in *this*
  game. So compute the player's side by matching `players.team_id` against the
  game's home/away ids; if it matches NEITHER, emit `player_team_side=None` and the
  frontend falls back to a plain (un-emphasized) matchup.

## API — additive, data-only (`api/schemas.py`, `api/main.py`)
Keep the API returning **raw resolved data**, not formatted strings — the frontend
owns nickname shortening + bolding, and `/parlay-builder/saved` is a Budgerr-
consumed surface (README §15.9), so additive full-name fields help them too.

`BuilderLegOut` (api/schemas.py:180) — add three OPTIONAL fields (additive, defaults
so every existing shape still validates):
```python
home_team: str | None = None      # full name, resolved from game_id
away_team: str | None = None      # full name, resolved from game_id
player_team_side: str | None = None  # "home" | "away" | None (team legs / traded / unresolved)
```

Enrich in BOTH handlers that build `BuilderLegOut` legs:
- `saved_builder_parlays` (`/parlay-builder/saved`, api/main.py:560).
- `builder_search` (`/parlay-builder`, api/main.py:484) — the live "Build" results
  must show teams too, or saved and search would be inconsistent.

Resolution must be **batched (no N+1)**:
1. Gather all leg `game_id`s across the parlays → ONE query:
   `SELECT g.game_id, g.home_team_id, g.away_team_id, ht.name AS home, at.name AS away
    FROM games g JOIN teams ht ON ht.team_id=g.home_team_id
    JOIN teams at ON at.team_id=g.away_team_id WHERE g.game_id = ANY(:ids)`.
2. Gather all player-leg `player_id`s → ONE query:
   `SELECT player_id, team_id FROM players WHERE player_id = ANY(:pids)`.
3. Per leg: set `home_team`/`away_team` from the game map; for player legs set
   `player_team_side` via a PURE helper `player_side(player_team_id, home_id, away_id)`
   → `"home"` if ==home, `"away"` if ==away, else `None`.

Factor `player_side` and the per-leg enrichment shaping into a small PURE helper
(DB-free, unit-testable) mirroring the `_shape_builder_record` pattern just added.
Missing game/player (empty map) → leave the fields `None`, never raise.

## Frontend — `web/` (READ PRODUCT.md, DESIGN.md, web/AGENTS.md/Next16 first)
- `web/app/lib/api.ts`: extend the `BuilderLeg` type (api.ts:103) with
  `home_team: string | null; away_team: string | null; player_team_side: "home" | "away" | null;`.
- New `web/app/lib/teamNames.ts`: the static 30-team MLB full-name→nickname map +
  a `nickname(fullName: string | null): string | null` (map lookup, fallback to the
  full name; null-safe). Export it for unit reuse.
- `web/app/builder/ConstructionList.tsx` is the SINGLE leg renderer (saved player
  parlays via BuilderControls, team parlays directly, and live "Build" results all
  flow through it — confirm while reading). Add a small secondary **matchup line per
  leg**, ABOVE the existing `.legLabel`:
  - Player leg with a resolved `player_team_side`: player's team **first + bold**,
    then `vs` opponent — `**{nickname(playerTeam)}** vs {nickname(oppTeam)}` where
    playerTeam = home_team if side==="home" else away_team.
  - Team leg, OR a player leg with `player_team_side===null`: plain
    `{nickname(away_team)} @ {nickname(home_team)}`.
  - If both team names are null (unresolved), render nothing (graceful).
  - Muted/secondary/small per DESIGN.md; add a `.legMatchup` (+ a bold
    `.legMatchupTeam`) class to `builder.module.css` following its token/naming
    conventions. Tabular not required (it's names). Do NOT add signal-green.

## Tests (`tests/`)
CRITICAL SAFETY: NO test DB; `ingestion.db.get_engine()` is LIVE. Use ONLY the pure
helpers and the `_FakeEngine` isolation from `tests/test_parlay_recommendations_api.py`
/ `tests/test_builder_record_api.py` (read them). Never write the live DB.
- PURE tests of `player_side`: home match→"home", away match→"away", neither
  (traded)→None.
- PURE tests of the enrichment shaping with fake game/player maps: player leg gets
  home_team/away_team + correct side; team leg gets both team names + side None;
  missing game_id → all None (no raise).
- ENDPOINT test via `_FakeEngine`: `saved_builder_parlays` now issues the main
  query THEN the games query THEN the players query — supply three result sets in
  that call order and assert the returned legs carry the resolved fields. (Confirm
  the exact query order while implementing and match the fixture to it.)
- A tiny JS/TS-free assertion is NOT required for `teamNames.ts` unless a JS test
  runner exists — there is none in this repo, so cover `nickname()` logic by keeping
  it trivial/obvious; the architect verifies it in the browser. (Do NOT add a JS
  test framework.)
- Full suite stays green (currently **268** at main HEAD); you are ADDING tests.

## Out of scope (architect does these)
- launchd, `:8000` kickstart, git push, live DB writes. Work in the worktree,
  commit there only. Architect reviews diffs, kickstarts the API (this DOES change
  `api/`), and browser-verifies both saved + live-build leg rows.
- Do NOT touch `/bet-performance`, `/parlay-recommendations`, `web/app/clv`, or the
  record endpoints from the sibling plan.
