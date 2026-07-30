# NBA Multi-Sport Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution note (this project):** bulk mechanical edits are delegated to cheap-model
> workers (`~/.claude/bin/delegate free|deepseek`) that can read/edit files but **cannot run
> shell, tests, or git**. The architect runs graphify, pytest, `tsc`/`next build`, live
> verification, the DB backfill, and all commits. So each task's "run test / commit" steps are
> the architect's; the worker produces the code shown.

**Goal:** NBA player-prop + game-market (spread/ML/total) parlays build, settle, and report
through the existing sport-parameterized builder — structurally verified now, live at tip-off
(~October).

**Architecture:** Inherit the NFL track wholesale (sport-parameterized builder / settlement /
chain / dashboard / record). Fill in NBA's market maps, close two `backfill.py` ingestion gaps
(write final scores; treat `AOT` as final), wire NBA into the daily chain (MLB-style daily
cadence) and the dashboard (NBA tab). No NBA model.

**Tech Stack:** Python 3.11 (`/Users/aayushpokhrel/dev/playstat/.venv`), SQLAlchemy + Postgres
(LIVE — no test DB), pytest, Next.js 16 (`web/`), SportsGameOdds + API-Sports basketball.

## Global Constraints

- **§15.8 guardrails (BINDING):** rank only on de-vigged market prob; `market_prob ≥ 0.55`; 2–4
  legs; across-game only; paper-only; NO "+EV"/"edge"/"value"/"beat the market" language; no
  signal-green (reserved for ≥75% joint prob).
- **No live DB in tests.** `ingestion.db.get_engine()` is LIVE. New tests are pure or use the
  fake-engine isolation pattern (`tests/test_builder_record_api.py`,
  `tests/test_parlay_builder_api.py`). A test that hits the live DB is a defect.
- **Additive-only** on every Budgerr surface (`/parlay-builder/saved`, `/edges`, `/box-scores`,
  `/games`, `/game-predictions`, `/parlay-recommendations`, `/bet-performance`) — README §7.1.
- **MLB + NFL byte-behavior unchanged.** Every NBA addition is a new map key / new branch;
  never alter an existing sport's config strings or code path.
- **graphify before reading source** (`graphify query "<q>"`); workers reading source in the
  main checkout should prefer graphify, else read the exact files named here.
- **Offseason:** live NBA odds are ~October. SGO betTypeIDs (`sp`/`ml`) + `SPREAD_LINE_FIELD` +
  the API-Sports score field path are **preseason `--dry-run`/probe-pinned by the architect**,
  not guessed into production.
- Every landed change updates README §11/§13/§14/§16 in the **same commit** and pushes.

---

## File Structure

- `ingestion/backfill.py` — MODIFY: `nba_team_points_rows` helper + `is_final`/AOT + wire into
  `backfill_games`. (Task 1)
- `ingestion/odds_ingest.py` — MODIFY: add `GAME_MARKETS['nba']`. (Task 2)
- `optimizer/builder.py` — MODIFY: `TEAM_MARKETS['nba']`, `SLATE_WINDOW_DAYS['nba']`,
  `_team_class`. (Task 2)
- `scripts/daily_chain.sh` — MODIFY: NBA daily build + settle block. (Task 3)
- `web/app/builder/SportTabs.tsx` — MODIFY: NBA tab. (Task 4)
- `web/app/builder/page.tsx` — MODIFY: `SPORT_CFG['nba']` + sport union. (Task 4)
- `web/app/lib/teamNames.ts` — MODIFY: 30 NBA nicknames. (Task 4)
- `tests/test_nba_ingest.py` — CREATE: pure tests for Task 1 + Task 2. (Tasks 1, 2)

---

### Task 1: NBA final-score writer + AOT handling (`ingestion/backfill.py`)

**Files:**
- Modify: `ingestion/backfill.py` (`backfill_games` ~L89-117; add helpers above it)
- Test: `tests/test_nba_ingest.py` (create)

**Interfaces:**
- Produces: `is_final(status: str | None) -> bool`; `nba_team_points_rows(game: dict, game_id:
  int, home_team_id: int, away_team_id: int) -> list[dict]` (each dict `{"team_id","game_id",
  "stat_type":"points","value":int}`; empty list if either score is None/missing);
  `current_nba_season(today: date | None = None) -> str` (e.g. `"2026-2027"`), and `main()`
  resolves the sentinel `--season current` through it. **`DEFAULT_SEASON` and the plain
  `--season` default stay `"2023-2024"` — unchanged — so the `com.playstat.backfill` launchd
  job and the historical backfill (Task 5) are byte-identical; only the daily chain opts into
  `--season current`.**

**Reference pattern:** `ingestion/nfl_backfill.py:202` `team_points_rows` (identical shape).

- [ ] **Step 1: Write the failing tests** — `tests/test_nba_ingest.py`:

```python
from ingestion.backfill import is_final, nba_team_points_rows


def test_is_final_recognizes_ft_and_aot():
    assert is_final("FT") is True
    assert is_final("AOT") is True   # after-overtime is a real final status (66 loaded games)
    assert is_final("NS") is False
    assert is_final("S") is False
    assert is_final(None) is False


def test_nba_team_points_rows_scored_game():
    game = {"scores": {"home": {"total": 112}, "away": {"total": 108}}}
    rows = nba_team_points_rows(game, game_id=500, home_team_id=10, away_team_id=20)
    assert rows == [
        {"team_id": 10, "game_id": 500, "stat_type": "points", "value": 112},
        {"team_id": 20, "game_id": 500, "stat_type": "points", "value": 108},
    ]


def test_nba_team_points_rows_missing_score_returns_empty():
    game = {"scores": {"home": {"total": None}, "away": {"total": 108}}}
    assert nba_team_points_rows(game, game_id=1, home_team_id=1, away_team_id=2) == []
    assert nba_team_points_rows({}, game_id=1, home_team_id=1, away_team_id=2) == []


def test_current_nba_season_labels_by_start_year():
    from datetime import date
    from ingestion.backfill import current_nba_season
    # NBA season is labelled by its start year; it spans ~Oct..Jun.
    assert current_nba_season(date(2026, 10, 25)) == "2026-2027"  # early season
    assert current_nba_season(date(2027, 4, 10)) == "2026-2027"   # playoffs, same season
    assert current_nba_season(date(2026, 7, 30)) == "2025-2026"   # offseason -> prior season
```

- [ ] **Step 2 (architect): run — expect FAIL** (`ImportError`):
  `/Users/aayushpokhrel/dev/playstat/.venv/bin/pytest tests/test_nba_ingest.py -q`

- [ ] **Step 3: Implement in `ingestion/backfill.py`** — add above `backfill_games`:

```python
# API-Sports basketball marks an over-time final as "AOT" (after over-time); a
# regulation final is "FT". Both are FINAL — the finished-games filter and score
# writing below must accept AOT or every OT game silently skips box scores + settle.
NBA_FINAL_STATUSES = {"FT", "AOT"}


def is_final(status):
    return status in NBA_FINAL_STATUSES


def nba_team_points_rows(game, game_id, home_team_id, away_team_id):
    """Pure: final-score rows for team_game_stats. Empty for an unscored game.
    Each team's final points is stored as a 'points' actual so settlement reads
    them like MLB runs_inning_1 / NFL points (mirrors nfl_backfill.team_points_rows).
    NOTE: the `scores.home.total` field path is architect-probe-confirmed against a
    live/historical API-Sports /games response before the backfill runs."""
    scores = game.get("scores") or {}
    home = (scores.get("home") or {}).get("total")
    away = (scores.get("away") or {}).get("total")
    if home is None or away is None:
        return []
    return [
        {"team_id": home_team_id, "game_id": game_id, "stat_type": "points", "value": int(home)},
        {"team_id": away_team_id, "game_id": game_id, "stat_type": "points", "value": int(away)},
    ]
```

Also add the current-season helper + sentinel resolution. Add near the top (after
`DEFAULT_SEASON`):

```python
from datetime import date as _date

def current_nba_season(today=None):
    """API-Sports season label ('YYYY-YYYY+1') for the NBA season in progress.
    NBA runs ~Oct..Jun and is labelled by its START year. Sep..Dec -> this year
    starts the season; Jan..Aug -> the prior year did. Lets the daily chain pass
    `--season current` so nightly score refresh always targets the live season
    without a hardcoded year (DEFAULT_SEASON stays 2023-2024 for the historical
    load + the self-disabling backfill job)."""
    today = today or _date.today()
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}-{start + 1}"
```

In `main()`, resolve the sentinel right after `args = parser.parse_args()`:
```python
    season = current_nba_season() if args.season == "current" else args.season
```
and pass `season` (not `args.season`) to `backfill_teams` / the `teams` query /
`backfill_players` / `backfill_games`. **The `--season` default is unchanged
(`DEFAULT_SEASON`)** — only the literal string `"current"` triggers the helper.

Then in `backfill_games`, change the finished filter (L99) and write scores in the upsert loop.
Current L99:
```python
    finished = [g for g in games if (g.get("status") or {}).get("short") == "FT"]
```
becomes:
```python
    finished = [g for g in games if is_final((g.get("status") or {}).get("short"))]
```
And inside `with engine.begin() as conn:` after the `games` upsert (mirror nfl_backfill L237),
add the team-score write:
```python
            for pr in nba_team_points_rows(
                game,
                game["id"] + offset,
                game["teams"]["home"]["id"] + offset,
                game["teams"]["away"]["id"] + offset,
            ):
                db.upsert(conn, "team_game_stats", ["team_id", "game_id", "stat_type"], pr)
```

- [ ] **Step 4 (architect): run — expect PASS.**
- [ ] **Step 5 (architect): commit** `feat(ingestion): NBA final scores + AOT-final handling (§16)`.

---

### Task 2: NBA market maps (`odds_ingest.py`, `builder.py`)

**Files:**
- Modify: `ingestion/odds_ingest.py` (`GAME_MARKETS` ~L71-84)
- Modify: `optimizer/builder.py` (`TEAM_MARKETS` L24-27, `SLATE_WINDOW_DAYS` L31, `_team_class`
  L34-38)
- Test: `tests/test_nba_ingest.py` (append)

**Interfaces:**
- Consumes: existing `bettype_for_market`, `MARKET_GEOMETRY` (both already generic over the
  `full_game_*` market names — no edit needed; NBA reuses the identical names).
- Produces: `GAME_MARKETS['nba']`, `TEAM_MARKETS['nba']`, `SLATE_WINDOW_DAYS['nba']=0`,
  `_team_class('nba') == 'game_tier'`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_nba_ingest.py`:

```python
from ingestion.odds_ingest import GAME_MARKETS, bettype_for_market
from optimizer.builder import TEAM_MARKETS, SLATE_WINDOW_DAYS, _team_class
from optimizer.builder_core import MARKET_GEOMETRY


def test_nba_game_markets_shape():
    assert GAME_MARKETS["nba"] == {
        "full_game_total": ("points", "all", "game"),
        "full_game_spread": ("points", "all", "game"),
        "full_game_moneyline": ("points", "all", "game"),
    }
    assert bettype_for_market("full_game_spread") == "sp"
    assert bettype_for_market("full_game_moneyline") == "ml"
    assert bettype_for_market("full_game_total") == "ou"


def test_nba_team_markets_and_geometry():
    assert TEAM_MARKETS["nba"] == (
        "full_game_total", "full_game_spread", "full_game_moneyline",
    )
    assert MARKET_GEOMETRY["full_game_total"] == "ou"
    assert MARKET_GEOMETRY["full_game_spread"] == "homeaway"
    assert MARKET_GEOMETRY["full_game_moneyline"] == "homeaway"


def test_nba_daily_cadence_and_game_tier_class():
    assert SLATE_WINDOW_DAYS["nba"] == 0          # daily like MLB, not NFL's weekly 4
    assert _team_class("nba") == "game_tier"       # shared with NFL
    assert _team_class("nfl") == "game_tier"
    assert _team_class("mlb") == "team_tier"       # unchanged
```

- [ ] **Step 2 (architect): run — expect FAIL** (`KeyError 'nba'`).

- [ ] **Step 3: Implement.** In `ingestion/odds_ingest.py` `GAME_MARKETS`, add after the `nfl`
  entry (before the closing `}`):

```python
    # NBA full-game markets: total/spread/moneyline, same shape as NFL (statID
    # points / entity all / period game; betTypeID ou/sp/ml). Live at ~October.
    "nba": {
        "full_game_total":     ("points", "all", "game"),
        "full_game_spread":    ("points", "all", "game"),
        "full_game_moneyline": ("points", "all", "game"),
    },
```

In `optimizer/builder.py`:
```python
TEAM_MARKETS = {
    "mlb": ("first_inning_runs", "f5_runs"),
    "nfl": ("full_game_total", "full_game_spread", "full_game_moneyline"),
    "nba": ("full_game_total", "full_game_spread", "full_game_moneyline"),
}
```
```python
SLATE_WINDOW_DAYS = {"mlb": 0, "nfl": 4, "nba": 0}
```
```python
def _team_class(sport):
    """--team-only save class: NFL AND NBA full-game markets save 'game_tier'
    (spread/ML/total), distinct from MLB's NRFI/F5 'team_tier'. Both kind='builder'."""
    return "game_tier" if sport in ("nfl", "nba") else "team_tier"
```

`MARKET_GEOMETRY` (`builder_core.py`) needs **no edit** — the `full_game_*` names are already
mapped. The test asserts this to lock it.

- [ ] **Step 4 (architect): run — expect PASS.**
- [ ] **Step 5 (architect): commit** `feat(builder): NBA market maps — game_tier, daily cadence (§16)`.

---

### Task 3: NBA daily chain wiring (`scripts/daily_chain.sh`)

**Files:** Modify: `scripts/daily_chain.sh` (the run block L124-135).

**No unit test** (shell). Architect verifies via `bash -n` + an isolated wrapper-semantics run.
**Delegate to `deepseek` (control surface — review hard); architect runs `bash -n` and confirms
placement.**

NBA is daily like MLB (no gate). Add an `_nba_daily_build` helper next to `_nfl_weekly_build`
(after L123) and wire NBA build (best-effort) + NBA scores (best-effort) into the chain AFTER
the MLB builder saves (so MLB's pre-game card is never delayed — §15.9 item 7A) and before
`settle`.

- [ ] **Step 1: Add the helper** after `_nfl_weekly_build` (L123):

```bash
	# NBA is DAILY (like MLB, no weekly gate) — it plays a single-day slate
	# (SLATE_WINDOW_DAYS nba=0). Build player + game-tier cards daily; scores +
	# settle run daily below. Best-effort (see the chain): an NBA failure is logged
	# but never aborts the MLB chain or pages. Live at season (~October).
	_nba_daily_build() {
		_step_retry nba_odds  "$PY" -m ingestion.odds_ingest --sport nba &&
			_step nba_builder_1.4 "$PY" -m optimizer.builder --sport nba --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nba_builder_2.0 "$PY" -m optimizer.builder --sport nba --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nba_game_1.4    "$PY" -m optimizer.builder --sport nba --team-only --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nba_game_2.0    "$PY" -m optimizer.builder --sport nba --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save
	}
```

The daily NBA score refresh uses `--season current` (Task 1's sentinel) so it always targets
the live season, never the hardcoded historical default.

- [ ] **Step 2: Wire into the chain.** After the NFL scores line (L134) and before
  `_step settle` (L135), insert the two NBA best-effort lines (each `&&`-chained, wrapped so a
  failure logs but continues — same pattern as the NFL lines):

```bash
		{ _nba_daily_build || echo "=== nba daily build: FAILED (non-fatal, MLB chain continues) ==="; } &&
		{ _step_retry nba_scores "$PY" -m ingestion.backfill --sport nba --only games --season current || echo "=== nba_scores: FAILED (non-fatal) ==="; } &&
```
so the tail reads: `… nfl_scores …} && { _nba_daily_build …} && { nba_scores …} && _step settle …`.

  **Confirmed:** `ingestion.backfill` accepts `--sport nba --only games` (`main()` L167-176)
  and, after Task 1, `--season current` (sentinel → `current_nba_season()`). `--only games`
  runs `backfill_games` only (no teams/players/stats), which upserts games + the Task 1 team
  scores.

- [ ] **Step 3 (architect): `bash -n scripts/daily_chain.sh`** → expect no output (syntax OK).
- [ ] **Step 4 (architect): isolated semantics check** — confirm a forced NBA-step failure
  still lets `settle` run (mirror the NFL gate/wrapper test approach), and MLB builder saves
  precede the NBA block.
- [ ] **Step 5 (architect): commit** `feat(chain): NBA daily build + settle, best-effort (§16)`.

---

### Task 4: Dashboard NBA tab (`web/`)

**Files:**
- Modify: `web/app/builder/SportTabs.tsx` (SPORTS array L4-7)
- Modify: `web/app/builder/page.tsx` (`SPORT_CFG` L12-40, sport union L48-49)
- Modify: `web/app/lib/teamNames.ts` (append 30 NBA nicknames to `TEAM_NICKNAMES`)

**Read `web/AGENTS.md` + `web/node_modules/next/dist/docs/` before writing Next code** (Next 16
differs from training data). **Delegate to `free`; architect runs `tsc` + `next build`.**

**Interfaces:** MLB + NFL configs untouched (byte-identical). NBA config: daily "Tonight's"
heading (like MLB), `tier2: "game"`, honest offseason empty state.

- [ ] **Step 1: `SportTabs.tsx`** — add NBA to SPORTS:

```tsx
const SPORTS: { key: string; label: string }[] = [
  { key: "mlb", label: "MLB" },
  { key: "nfl", label: "NFL" },
  { key: "nba", label: "NBA" },
];
```

- [ ] **Step 2: `page.tsx`** — add the `nba` key to `SPORT_CFG` after the `nfl` entry (inside
  the object, before `} as const;`):

```tsx
  nba: {
    tier2: "game" as const,
    playerHeading: "Tonight's low-risk parlays",
    tier2Heading: "Game-market parlays",
    tier2Note:
      "Full-game total / spread / moneyline. Moneyline favorites can clear the safety floor; totals and spreads price near a coin flip, so this tier is higher-variance and may be empty.",
    tier2Empty: {
      title: "No game-market parlays tonight",
      body: "Spreads and totals rarely clear the safety floor and moneyline favorites are picked sparingly — an empty night here is normal.",
    },
    emptyAll: {
      title: "No NBA parlays yet",
      body: "The nightly NBA card builds once the season opens (~October). Check back then.",
    },
  },
```
And widen the sport union (L48):
```tsx
  const sport: "mlb" | "nfl" | "nba" =
    sportParam === "nfl" ? "nfl" : sportParam === "nba" ? "nba" : "mlb";
```

- [ ] **Step 3: `teamNames.ts`** — append these 30 entries to `TEAM_NICKNAMES` (exact DB
  names; update the leading comment to say MLB + NFL + NBA):

```ts
  "Atlanta Hawks": "Hawks",
  "Boston Celtics": "Celtics",
  "Brooklyn Nets": "Nets",
  "Charlotte Hornets": "Hornets",
  "Chicago Bulls": "Bulls",
  "Cleveland Cavaliers": "Cavaliers",
  "Dallas Mavericks": "Mavericks",
  "Denver Nuggets": "Nuggets",
  "Detroit Pistons": "Pistons",
  "Golden State Warriors": "Warriors",
  "Houston Rockets": "Rockets",
  "Indiana Pacers": "Pacers",
  "Los Angeles Clippers": "Clippers",
  "Los Angeles Lakers": "Lakers",
  "Memphis Grizzlies": "Grizzlies",
  "Miami Heat": "Heat",
  "Milwaukee Bucks": "Bucks",
  "Minnesota Timberwolves": "Timberwolves",
  "New Orleans Pelicans": "Pelicans",
  "New York Knicks": "Knicks",
  "Oklahoma City Thunder": "Thunder",
  "Orlando Magic": "Magic",
  "Philadelphia 76ers": "76ers",
  "Phoenix Suns": "Suns",
  "Portland Trail Blazers": "Trail Blazers",
  "Sacramento Kings": "Kings",
  "San Antonio Spurs": "Spurs",
  "Toronto Raptors": "Raptors",
  "Utah Jazz": "Jazz",
  "Washington Wizards": "Wizards",
```

  (If `TEAM_NICKNAMES` already contains an NFL block, append NBA after it. Both LA teams share
  "Los Angeles" but map to distinct nicknames — the reason the lookup table exists.)

- [ ] **Step 4 (architect): `cd web && npm run typecheck`** (or `npx tsc --noEmit`) **and
  `npm run build`** → expect clean.
- [ ] **Step 5 (architect): commit** `feat(web): NBA builder tab + team nicknames (§16)`.

---

### Task 5: Historical final-score backfill + live verification (ARCHITECT ONLY — not delegated)

**Files:** none (live DB writes; architect reserved lane).

- [ ] **Step 1: Probe the score field path.** Fetch one historical API-Sports basketball
  `/games` response (via `ingestion.api_client` / a `--dry-run`-style probe) and confirm
  `scores.home.total` / `scores.away.total` carry the final points. If the path differs, fix
  `nba_team_points_rows` before running the backfill.
- [ ] **Step 2: Run the historical backfill** — re-run the NBA games ingest for the loaded
  season with the explicit historical season (NOT `current`):
  `ingestion.backfill --sport nba --only games --season 2023-2024` (this is also the default,
  but pass it explicitly for clarity) so `team_game_stats('points')` is populated for all 1,376
  games. Idempotent (upsert on `team_id/game_id/stat_type`). (DB shows one loaded season,
  2023-10→2024-06; if any earlier season is present, loop it too.)
- [ ] **Step 3: Verify the backfill** — `team_game_stats` now has ~2,752 NBA rows (2/game);
  spot-check ≥3 games' scores against a known final; confirm AOT games loaded box scores +
  scores; check no FK orphans; confirm a second run is a no-op (idempotent).
- [ ] **Step 4: Verify game-tier settlement against a real score** — construct/settle a game
  tier leg (total or spread) for one finished NBA game with `game_lines` present (or a fixture
  standing in for the offseason-absent odds) and confirm the pure scoring returns the correct
  hit/miss and that a push voids. (If no NBA `game_lines` exist offseason, this is a fixture/
  unit-level check of the sport-generic scoring on NBA final scores.)
- [ ] **Step 5: Structural builder run** — `optimizer.builder --sport nba` (player) and
  `--sport nba --team-only` run without crashing (0 legs until odds land offseason); confirm
  MLB/NFL builder byte-behavior unchanged (spot a live MLB build).

---

## Wrap-up (architect)

- [ ] Full suite: `/Users/aayushpokhrel/dev/playstat/.venv/bin/pytest -q` → 352 + new NBA tests green.
- [ ] `graphify update .` (AST-only, free).
- [ ] README §11/§13 (§14.3 companion NBA note or new §14 entry) + §16 roadmap item 3 → BUILT,
  updated in the final commit.
- [ ] Merge to main + push. `launchctl kickstart -k gui/$(id -u)/com.playstat.api` only if an
  API-imported module changed (odds_ingest/builder/backfill are chain CLIs the API does not
  import — confirm before deciding a kickstart is needed).
- [ ] Loose end reminder to user: dashboard NBA tab VISUAL is behind login (architect can't
  enter the password) — a human eyeballs `/builder?sport=nba` once.

## Self-Review

- **Spec coverage:** G1 (Task 1 scores) ✓, G2 (Task 1 AOT) ✓, GAME_MARKETS/TEAM_MARKETS/
  SLATE_WINDOW_DAYS/_team_class (Task 2) ✓, chain daily+best-effort (Task 3) ✓, dashboard tab +
  SPORT_CFG + teamNames (Task 4) ✓, record `?sport=nba` (no code — spec §Record, asserted via
  existing COALESCE; verified in Task 5 Step 5 region) ✓, historical backfill + settlement
  verify (Task 5) ✓, tests (Tasks 1/2) ✓, guardrails (Global Constraints) ✓.
- **Placeholder scan:** none — all code shown; the two probe-pinned values (score field path,
  sp/ml IDs) are explicitly architect-verified steps, not code placeholders.
- **Type consistency:** `is_final`/`nba_team_points_rows`/`current_nba_season`/`_team_class`/
  `SLATE_WINDOW_DAYS`/`TEAM_MARKETS`/`GAME_MARKETS` names identical across tasks and tests.
  `full_game_*` market names reused verbatim so `MARKET_GEOMETRY`/`bettype_for_market` need no
  edit (locked by test). `--season current` sentinel (Task 1) consumed by Task 3's chain line.
- **Live-correctness:** daily `nba_scores` targets the live season via `--season current`;
  `DEFAULT_SEASON`/plain default unchanged so `com.playstat.backfill` + historical backfill are
  byte-identical.
