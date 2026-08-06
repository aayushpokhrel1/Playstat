# MLS #2 — Builder Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **Execution note:** mechanical edits delegated to cheap workers (`~/.claude/bin/delegate free`
> for maps/dashboard, `deepseek` for the chain control surface) that read/edit files but
> **cannot run shell/tests/git**. The architect runs graphify, pytest, `tsc`/`next build`, live
> verification, and all commits.
>
> **graphify before reading source** (`graphify query`) — repo rule.

**Goal:** Wire the loaded MLS soccer data (#1) into the sport-parameterized builder — odds maps,
settlement, chain, dashboard — so MLS player-prop (shots/shots-on-goal/tackles) + match-total
parlays build/settle/report, NBA-style. Structurally verified now (settlement on real 2022–24
stats); live at a paid API-Sports plan + live odds.

**Architecture:** Inherit the NBA build wholesale. MLS reuses the existing `full_game_total`
market name for its match total, so `MARKET_GEOMETRY`/`bettype_for_market`/`game_tier`/settlement
total-scoring need **no new plumbing**. Only NEW behavior: soccer's `AET`/`PEN` final statuses in
settlement, and the soccer stat/odds map entries + MLS dashboard tab.

**Tech Stack:** Python 3.11 (`.venv`), Postgres (LIVE — no test DB), pytest, Next 16 (`web/`),
SportsGameOdds (`odds_league_id="MLS"`), API-Sports football (settlement stats, #1).

## Global Constraints
- **§15.8 guardrails BINDING** (rank on devig market prob, `≥0.55`, 2–4 legs, across-game,
  paper-only, no +EV/edge/green).
- **No live DB in tests** (fake-engine/pure only — `tests/test_parlay_builder_api.py` pattern).
- **Additive-only; MLB/NBA/NFL byte-unchanged.** Every MLS addition is a new map key / branch.
- **Budgerr-safe** (default `mlb` everywhere; MLS is a new `sport` value it never requests).
- Live is paid-gated (spec §paid-gate); the chain block is inert on free until then.
- README §16.4 updated + pushed in the landing commit.

## File Structure
- `ingestion/odds_ingest.py` — `STAT_MAPS['mls']` + `GAME_MARKETS['mls']`. (Task 1, free)
- `optimizer/builder.py` — `TEAM_MARKETS['mls']`, `SLATE_WINDOW_DAYS['mls']`, `_team_class`. (Task 1, free)
- `modeling/settle.py` — extend `_FINAL_GAME_STATUSES` with `AET`/`PEN`. (Task 1, free)
- `scripts/daily_chain.sh` — `_mls_daily_build` + wiring. (Task 2, deepseek)
- `web/app/builder/SportTabs.tsx`, `page.tsx`, `web/app/lib/teamNames.ts` — MLS tab/config/nicknames. (Task 3, free)
- `tests/test_mls_wiring.py` — CREATE: pure map + settlement tests. (Task 1)

---

### Task 1: odds + builder maps + soccer final statuses (delegate: free)

**Files:** Modify `ingestion/odds_ingest.py`, `optimizer/builder.py`, `modeling/settle.py`;
Create `tests/test_mls_wiring.py`.

**Interfaces (produced):** `STAT_MAPS['mls']`, `GAME_MARKETS['mls']` (reuses key
`full_game_total`), `TEAM_MARKETS['mls']`, `SLATE_WINDOW_DAYS['mls']=0`,
`_team_class('mls')=='game_tier'`, `_FINAL_GAME_STATUSES ⊇ {AET,PEN}`.

- [ ] **Step 1: Write failing tests** — `tests/test_mls_wiring.py`:

```python
def test_mls_stat_and_game_markets():
    from ingestion.odds_ingest import STAT_MAPS, GAME_MARKETS
    assert STAT_MAPS["mls"] == {
        "shots": "shots", "shots_onGoal": "shots_on_goal", "tackles": "tackles",
    }
    # match total reuses the existing full_game_total market name (zero new plumbing)
    assert GAME_MARKETS["mls"] == {"full_game_total": ("points", "all", "game")}


def test_mls_builder_wiring():
    from optimizer.builder import TEAM_MARKETS, SLATE_WINDOW_DAYS, _team_class
    from optimizer.builder_core import MARKET_GEOMETRY
    assert TEAM_MARKETS["mls"] == ("full_game_total",)
    assert SLATE_WINDOW_DAYS["mls"] == 0            # daily like MLB/NBA
    assert _team_class("mls") == "game_tier"
    assert MARKET_GEOMETRY["full_game_total"] == "ou"  # reused, no edit needed


def test_soccer_extra_time_settles_as_final():
    from modeling.settle import leg_status
    assert leg_status("AET", 3) == "ready"   # after extra time -> final
    assert leg_status("PEN", 2) == "ready"   # penalty shootout -> final
    assert leg_status("AET", None) == "void" # final but no stat -> void
    assert leg_status("HT", 1) == "pending"  # half time -> not final
    # existing finals unchanged
    assert leg_status("FT", 1) == "ready"
    assert leg_status("AOT", 1) == "ready"
```

- [ ] **Step 2 (architect): run — expect FAIL** (`KeyError 'mls'`).

- [ ] **Step 3: Implement.** In `ingestion/odds_ingest.py`, add to `STAT_MAPS` (after `nfl`):
```python
    # MLS (soccer) player props — SGO statID -> player_game_stats.stat_type
    # (values match ingestion/soccer_backfill.extract_soccer_player_stats).
    "mls": {
        "shots": "shots",
        "shots_onGoal": "shots_on_goal",
        "tackles": "tackles",
    },
```
and to `GAME_MARKETS` (after `nba`):
```python
    # MLS match total goals — reuses full_game_total (statID points / ou / game)
    # so geometry + total-scoring settlement need no new plumbing. Skip ml3way
    # (3-way, doesn't fit two-sided geometry), spread, 2-way ml for v1.
    "mls": {
        "full_game_total": ("points", "all", "game"),
    },
```

In `optimizer/builder.py`:
```python
TEAM_MARKETS = {
    "mlb": ("first_inning_runs", "f5_runs"),
    "nfl": ("full_game_total", "full_game_spread", "full_game_moneyline"),
    "nba": ("full_game_total", "full_game_spread", "full_game_moneyline"),
    "mls": ("full_game_total",),
}
```
```python
SLATE_WINDOW_DAYS = {"mlb": 0, "nfl": 4, "nba": 0, "mls": 0}
```
```python
def _team_class(sport):
    """--team-only save class: NFL/NBA/MLS full-game markets save game_tier,
    distinct from MLB NRFI/F5 team_tier. Both kind=builder."""
    return "game_tier" if sport in ("nfl", "nba", "mls") else "team_tier"
```

In `modeling/settle.py`, extend the final-status set (soccer adds AET/PEN):
```python
# Final game statuses. MLB/NFL emit only "FT"; NBA adds "AOT" (after over-time);
# MLS (soccer) adds "AET" (after extra time) and "PEN" (penalty shootout). All
# are final and MUST settle. Non-soccer sports never emit AET/PEN, so this is
# additive for them.
_FINAL_GAME_STATUSES = {"FT", "AOT", "AET", "PEN"}
```
(Update the surrounding comment if it names only FT/AOT.)

- [ ] **Step 4 (architect): run — expect PASS.**
- [ ] **Step 5 (architect): commit** `feat(builder): MLS odds/builder maps + soccer AET/PEN settle (§16)`.

---

### Task 2: MLS daily chain block (delegate: deepseek — control surface)

**Files:** Modify `scripts/daily_chain.sh`. No unit test (shell); architect runs `bash -n` + a
best-effort semantics check.

MLS is daily like MLB/NBA (no gate). Add `_mls_daily_build` next to `_nba_daily_build`, wire it
best-effort after the NBA block and before `settle`. Live only with the paid plan (inert on free
— odds/games absent → 0 legs, and the score refresh is paid-gated but non-fatal).

- [ ] **Step 1: Add the helper** after `_nba_daily_build` (mirror it exactly):
```bash
	# MLS (soccer) — DAILY like NBA. Player (shots/tackles) + match-total game
	# tier. Best-effort: an MLS failure logs but never aborts the MLB chain.
	# Live only with a paid API-Sports plan (current-season stats); inert on free.
	_mls_daily_build() {
		_step_retry mls_odds  "$PY" -m ingestion.odds_ingest --sport mls &&
			_step mls_builder_1.4 "$PY" -m optimizer.builder --sport mls --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step mls_builder_2.0 "$PY" -m optimizer.builder --sport mls --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step mls_game_1.4    "$PY" -m optimizer.builder --sport mls --team-only --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step mls_game_2.0    "$PY" -m optimizer.builder --sport mls --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save
	}
```

- [ ] **Step 2: Wire in** — after the `nba_scores` line and before `_step settle`, insert
  (mirror the NBA best-effort wrappers; MLS score refresh is `soccer_backfill --season current
  --only fixtures`, paid-gated so best-effort/non-fatal):
```bash
		{ _mls_daily_build || echo "=== mls daily build: FAILED (non-fatal, MLB chain continues) ==="; } &&
		{ _step_retry mls_scores "$PY" -m ingestion.soccer_backfill --season 2024 --only fixtures || echo "=== mls_scores: FAILED (non-fatal) ==="; } &&
```
  NOTE: `--season 2024` is a **placeholder** for the free/structural era (current season is paid;
  `soccer_backfill` has no `current` sentinel). When the paid plan lands, change this to the live
  season (or add a `current` sentinel to `soccer_backfill`, like `current_nba_season`). The
  architect decides the exact token in review; keep it best-effort/non-fatal regardless.

- [ ] **Step 3 (architect): `bash -n scripts/daily_chain.sh`** → no output.
- [ ] **Step 4 (architect): semantics check** — a forced MLS failure still lets `settle` run;
  MLB builder saves precede the MLS block.
- [ ] **Step 5 (architect): commit** `feat(chain): MLS daily build + settle, best-effort (§16)`.

---

### Task 3: dashboard MLS tab (delegate: free)

**Files:** Modify `web/app/builder/SportTabs.tsx`, `web/app/builder/page.tsx`,
`web/app/lib/teamNames.ts`. Read `web/AGENTS.md` first (Next 16). MLB/NFL/NBA byte-unchanged.

- [ ] **Step 1: `SportTabs.tsx`** — add `{ key: "mls", label: "MLS" }` to the SPORTS array
  (after nba).

- [ ] **Step 2: `page.tsx`** — add the `mls` key to `SPORT_CFG` after `nba` (inside the object):
```tsx
  mls: {
    tier2: "game" as const,
    playerHeading: "Today's low-risk parlays",
    tier2Heading: "Match-total parlays",
    tier2Note:
      "Match total goals (over/under). Overs on high-scoring matchups can clear the safety floor; most price near a coin flip, so this tier is higher-variance and may be empty.",
    tier2Empty: {
      title: "No match-total parlays today",
      body: "Match totals rarely clear the safety floor — an empty day here is normal.",
    },
    emptyAll: {
      title: "No MLS parlays yet",
      body: "MLS parlays build daily during the season once live odds + settlement data are connected.",
    },
  },
```
  and widen the sport union (currently `"mlb" | "nfl" | "nba"`):
```tsx
  const sport: "mlb" | "nfl" | "nba" | "mls" =
    sportParam === "nfl" ? "nfl" : sportParam === "nba" ? "nba" : sportParam === "mls" ? "mls" : "mlb";
```

- [ ] **Step 3: `teamNames.ts`** — append these 29 MLS entries to `TEAM_NICKNAMES` (exact DB
  names) and update the leading comment to include MLS:
```ts
  "Atlanta United FC": "Atlanta Utd",
  "Austin": "Austin",
  "CF Montreal": "Montréal",
  "Charlotte": "Charlotte",
  "Chicago Fire": "Fire",
  "Colorado Rapids": "Rapids",
  "Columbus Crew": "Crew",
  "DC United": "D.C. United",
  "FC Cincinnati": "Cincinnati",
  "FC Dallas": "Dallas",
  "Houston Dynamo": "Dynamo",
  "Inter Miami": "Inter Miami",
  "Los Angeles FC": "LAFC",
  "Los Angeles Galaxy": "LA Galaxy",
  "Minnesota United FC": "Minnesota Utd",
  "Nashville SC": "Nashville",
  "New England Revolution": "Revolution",
  "New York City FC": "NYCFC",
  "New York Red Bulls": "Red Bulls",
  "Orlando City SC": "Orlando City",
  "Philadelphia Union": "Union",
  "Portland Timbers": "Timbers",
  "Real Salt Lake": "RSL",
  "San Jose Earthquakes": "Earthquakes",
  "Seattle Sounders": "Sounders",
  "Sporting Kansas City": "Sporting KC",
  "St. Louis City": "St. Louis",
  "Toronto FC": "Toronto",
  "Vancouver Whitecaps": "Whitecaps",
```

- [ ] **Step 4 (architect): `cd web && npx tsc --noEmit` + `npm run build`** → clean.
- [ ] **Step 5 (architect): commit** `feat(web): MLS builder tab + team nicknames (§16)`.

---

### Task 4: verification (ARCHITECT ONLY)

- [ ] **Step 1: SGO MLS `--dry-run` statID pin** — `python -m ingestion.odds_ingest --sport mls
  --dry-run` (MLS is in season now, so the live SGO feed has MLS events): confirm the coverage
  report recognizes `shots`/`shots_onGoal`/`tackles` + the `full_game_total` (`points`/`ou`/`game`)
  market. If a statID differs from the map, fix `STAT_MAPS['mls']`/`GAME_MARKETS['mls']` (cheap
  SGO entities). This does NOT save (no current MLS games in the DB to match — historical only),
  but pins the maps against the live feed.
- [ ] **Step 2: structural builder run** — `optimizer.builder --sport mls` (player) and
  `--sport mls --team-only` run clean (0 legs — no MLS odds/current games; no crash).
- [ ] **Step 3: settlement on real MLS stats** — verify (fixture/unit-level, reading a real
  loaded 2022–24 game) that a shots-over player leg settles hit/miss against `player_game_stats`
  and a match-total leg settles against `team_game_stats('points')` (total goals) — proving the
  soccer settlement path works on real data even without odds. Add as a regression test if clean.
- [ ] **Step 4: isolation** — MLB/NBA/NFL builder byte-behavior unchanged; Budgerr `saved`/
  `record` (no-sport, tier=all/team/player) unchanged; full pytest green.

## Wrap-up (architect)
- [ ] `graphify update .`; README §16.4 MLS #2 BUILT (+ Champions League noted as the next small
  track); push. API kickstart (`optimizer/builder.py` + `modeling/settle.py` are API-imported).
- [ ] Dashboard MLS-tab visual: eyeball via the login-disabled dev preview (`SESSION_SECRET=`
  empty) — honest empty state.

## Self-Review
- **Spec coverage (§MLS #2):** STAT_MAPS/GAME_MARKETS ✓ (T1); TEAM_MARKETS/geometry/window/
  _team_class ✓ (T1); AET/PEN settlement ✓ (T1); match_total reuse of full_game_total (decision
  resolved: reuse) ✓; chain ✓ (T2); dashboard + nicknames ✓ (T3); record no-change ✓; structural
  + settlement verification ✓ (T4); MLB/NBA/NFL byte-unchanged ✓; Budgerr-safe ✓.
- **Placeholder scan:** none — the chain `--season 2024` placeholder is explicitly flagged as an
  architect-review decision (paid-era token), not a code gap.
- **Type consistency:** `full_game_total` reused verbatim (locked by test); stat_type values
  (`shots`/`shots_on_goal`/`tackles`) match #1's `extract_soccer_player_stats` output;
  `_FINAL_GAME_STATUSES` extension is additive, MLB/NBA/NFL never emit AET/PEN.
