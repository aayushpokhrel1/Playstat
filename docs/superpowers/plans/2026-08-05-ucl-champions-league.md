# UCL — Champions League (New Soccer League) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **Execution note:** mechanical edits delegated to cheap workers (`~/.claude/bin/delegate free`
> for maps/dashboard/config, `deepseek` for the backfill parameterization + chain control
> surface) that read/edit files but **cannot run shell/tests/git**. The architect runs
> graphify, pytest, `tsc`/`next build`, the LIVE backfill, live verification, and all commits.
>
> **graphify before reading source** (`graphify query`) — repo rule.

**Goal:** Add the UEFA Champions League as a second soccer league (`sport='ucl'`, id_offset
+400M) reusing the entire MLS soccer pipeline — parameterize the (currently MLS-hardcoded)
`soccer_backfill` by `--sport`, add `ucl` odds/builder map keys, a daily chain block, and a
dashboard tab — so UCL player-prop (shots/shots-on-goal/tackles) + match-total parlays
build/settle/report exactly like MLS, structurally verified on free 2022–2024 data.

**Architecture:** Inherit MLS wholesale. UCL reuses the shared soccer helpers
(`is_soccer_final`, `soccer_team_points_rows`, `extract_soccer_player_stats` — already
sport-agnostic) and the `full_game_total` market (statID points/ou/game). The ONE structural
change is threading `sport` through `soccer_backfill` (default `mls`, so MLS stays byte-identical).
**`modeling/settle.py` needs NO change** — soccer's `{AET,PEN}` final statuses already landed
with MLS #2. Everything else is a new additive map key / branch / tab.

**Tech Stack:** Python 3.11 (`.venv`), Postgres (LIVE — no test DB), pytest, Next 16 (`web/`),
SportsGameOdds (`odds_league_id="UEFA_CHAMPIONS_LEAGUE"`, free tier per README §16.4),
API-Sports football (`league 2`, seasons 2022–24 free — LIVE-VERIFIED 2026-08-05: 279 fixtures
for 2024, FT/AET/PEN present).

## Global Constraints
- **§15.8 guardrails BINDING** (rank on devig market prob, `≥0.55`, 2–4 legs, across-game,
  paper-only, no +EV/edge/green).
- **No live DB in tests** (fake-engine/pure only — `tests/test_soccer_ingest.py` pattern).
- **Additive-only; MLB/NBA/NFL/MLS byte-unchanged.** Every UCL addition is a new map key /
  branch / default-valued param. `soccer_backfill` parameterization MUST default `sport="mls"`
  so all existing 3-arg calls + `tests/test_soccer_ingest.py` pass unchanged.
- **Budgerr-safe** (default `mlb` everywhere; `ucl` is a new `sport` value it never requests).
- Live is paid-gated (current-season stats are paid — same API-Sports gate as MLS/NBA); the
  chain block is inert on free until then.
- README §16.4 updated + pushed in the landing commit. API kickstart after landing
  (`optimizer/builder.py` is API-imported; `soccer_backfill`/`odds_ingest` are not, but the
  builder map change is).

## File Structure
- `ingestion/config.py` — `SPORTS['ucl']` (football host, league 2, `UEFA_CHAMPIONS_LEAGUE`, +400M). (Task 1)
- `ingestion/soccer_backfill.py` — thread `sport` (default `"mls"`) through the 3 backfill fns + `--sport` CLI. (Task 1)
- `ingestion/odds_ingest.py` — `STAT_MAPS['ucl']` + `GAME_MARKETS['ucl']` (mirror `mls`). (Task 2)
- `optimizer/builder.py` — `TEAM_MARKETS['ucl']`, `SLATE_WINDOW_DAYS['ucl']=0`, `_team_class` += ucl. (Task 2)
- `scripts/daily_chain.sh` — `_ucl_daily_build` + best-effort wiring. (Task 3, deepseek)
- `web/app/builder/SportTabs.tsx`, `page.tsx`, `web/app/lib/teamNames.ts` — UCL tab/config/nicknames. (Task 4, free)
- `tests/test_ucl_wiring.py` — CREATE: pure map + parameterized-backfill tests. (Tasks 1–2)

---

### Task 1: config + soccer_backfill parameterization (delegate: deepseek — the one refactor)

**Files:** Modify `ingestion/config.py`, `ingestion/soccer_backfill.py`; Create `tests/test_ucl_wiring.py`.

**Interfaces (produced):** `SPORTS['ucl']` (id_offset `400_000_000`, league_id `2`,
odds_league_id `"UEFA_CHAMPIONS_LEAGUE"`); `soccer_backfill.backfill_fixtures(client, engine,
season, sport="mls")`, `backfill_teams_and_games(conn, fixtures, offset, sport="mls")`,
`backfill_player_stats(client, engine, finished, sport="mls")`, `main` gains `--sport
{mls,ucl}` (default `mls`).

- [ ] **Step 1: Write failing tests** — `tests/test_ucl_wiring.py`:

```python
def test_ucl_config_entry():
    from ingestion.config import SPORTS
    assert SPORTS["ucl"]["id_offset"] == 400_000_000
    assert SPORTS["ucl"]["league_id"] == 2
    assert SPORTS["ucl"]["odds_league_id"] == "UEFA_CHAMPIONS_LEAGUE"
    assert SPORTS["ucl"]["base_url"] == "https://v3.football.api-sports.io"


def test_soccer_backfill_sport_param_defaults_to_mls(monkeypatch):
    # Default (no sport arg) is byte-identical MLS behavior: sport='mls', +300M offset.
    import ingestion.soccer_backfill as sb
    calls = []
    monkeypatch.setattr(sb.db, "upsert", lambda conn, t, c, r: calls.append((t, r)))
    fixtures = [{
        "fixture": {"id": 7, "date": "2024-07-04T00:00:00+00:00", "status": {"short": "FT"}},
        "teams": {"home": {"id": 11, "name": "A"}, "away": {"id": 22, "name": "B"}},
        "goals": {"home": 2, "away": 0},
    }]
    sb.backfill_fixtures(_FakeClient(fixtures, []), _FakeEngine(), 2024)  # no sport
    game_row = next(r for t, r in calls if t == "games")
    assert game_row["sport"] == "mls" and game_row["game_id"] == 7 + 300_000_000


def test_soccer_backfill_sport_ucl_uses_offset_and_sport(monkeypatch):
    import ingestion.soccer_backfill as sb
    calls = []
    monkeypatch.setattr(sb.db, "upsert", lambda conn, t, c, r: calls.append((t, r)))
    fixtures = [{
        "fixture": {"id": 7, "date": "2024-09-17T00:00:00+00:00", "status": {"short": "FT"}},
        "teams": {"home": {"id": 11, "name": "Real Madrid"}, "away": {"id": 22, "name": "Milan"}},
        "goals": {"home": 3, "away": 1},
    }]
    sb.backfill_fixtures(_FakeClient(fixtures, []), _FakeEngine(), 2024, sport="ucl")
    game_row = next(r for t, r in calls if t == "games")
    team_row = next(r for t, r in calls if t == "teams")
    assert game_row["sport"] == "ucl" and game_row["game_id"] == 7 + 400_000_000
    assert team_row["sport"] == "ucl" and team_row["team_id"] == 11 + 400_000_000
```

Reuse the fake-engine/fake-client helpers by importing them; add at the TOP of the new file:

```python
from tests.test_soccer_ingest import _FakeConn, _FakeEngine, _FakeClient  # shared fakes
```

- [ ] **Step 2 (architect): run — expect FAIL** (`KeyError 'ucl'` / unexpected `sport` kwarg).

- [ ] **Step 3: Implement.** In `ingestion/config.py`, add to the `SPORTS` dict AFTER the `mls`
  entry (before the closing `}`):
```python
    # UEFA Champions League (soccer) — SAME API-Sports FOOTBALL host/key as MLS,
    # league 2 (LIVE-VERIFIED 2026-08-05: 279 fixtures/2024, FT/AET/PEN). Free tier:
    # seasons 2022-2024 only (current is paid). Ingested by soccer_backfill --sport ucl.
    "ucl": {
        "base_url": "https://v3.football.api-sports.io",
        "league_id": 2,
        "odds_league_id": "UEFA_CHAMPIONS_LEAGUE",
        "id_offset": 400_000_000,
    },
```

  In `ingestion/soccer_backfill.py`, replace the module constant `SPORT = "mls"` (keep it as the
  DEFAULT everywhere) by threading a `sport` parameter with default `"mls"`. Concretely:

  - Delete the `SPORT = "mls"` line? **NO** — keep `SPORT = "mls"` as the default sentinel used
    by the CLI default only; but the FUNCTIONS must take `sport="mls"` params (not read the
    global), so change every `SPORT` reference inside the functions to the local `sport` param:

```python
def backfill_teams_and_games(conn, fixtures, offset, sport="mls"):
    """Upsert teams (from fixture home/away) + games; write scores for finals."""
    for fx in fixtures:
        f = fx["fixture"]; teams = fx["teams"]
        for side in ("home", "away"):
            t = teams[side]
            db.upsert(conn, "teams", ["team_id"],
                      {"team_id": t["id"] + offset, "sport": sport, "name": t["name"]})
        game_id = f["id"] + offset
        status = (f.get("status") or {}).get("short")
        db.upsert(conn, "games", ["game_id"], {
            "game_id": game_id, "sport": sport, "date": f["date"][:10],
            "home_team_id": teams["home"]["id"] + offset,
            "away_team_id": teams["away"]["id"] + offset,
            "status": status,
        })
        if is_soccer_final(status):
            for pr in soccer_team_points_rows(
                fx, game_id, teams["home"]["id"] + offset, teams["away"]["id"] + offset
            ):
                db.upsert(conn, "team_game_stats", ["team_id", "game_id", "stat_type"], pr)


def backfill_fixtures(client, engine, season, sport="mls"):
    offset = SPORTS[sport]["id_offset"]
    fixtures = client.get("/fixtures", params={"league": SPORTS[sport]["league_id"], "season": season})
    with engine.begin() as conn:
        backfill_teams_and_games(conn, fixtures, offset, sport)
    finished = [fx for fx in fixtures if is_soccer_final((fx["fixture"].get("status") or {}).get("short"))]
    print(f"fixtures {season}: upserted {len(fixtures)} ({len(finished)} finished)")
    return finished


def backfill_player_stats(client, engine, finished_fixtures, sport="mls"):
    offset = SPORTS[sport]["id_offset"]
    with engine.begin() as conn:
        already = db.game_ids_with_stats(conn)
    remaining = [fx for fx in finished_fixtures if fx["fixture"]["id"] + offset not in already]
    print(f"player_stats: {len(already)} games already loaded, {len(remaining)} remaining")
    loaded = 0
    for fx in remaining:
        game_id = fx["fixture"]["id"] + offset
        teams = client.get("/fixtures/players", params={"fixture": fx["fixture"]["id"]})
        with engine.begin() as conn:
            for team_block in teams:
                team_id = team_block["team"]["id"] + offset
                for p in team_block.get("players", []):
                    pid = p["player"]["id"] + offset
                    db.upsert(conn, "players", ["player_id"], {
                        "player_id": pid, "sport": sport, "name": p["player"]["name"],
                        "team_id": team_id, "position": None,
                    })
                    stats = (p.get("statistics") or [{}])[0]
                    for stat_type, value in extract_soccer_player_stats(stats).items():
                        db.upsert(conn, "player_game_stats",
                                  ["player_id", "game_id", "stat_type"],
                                  {"player_id": pid, "game_id": game_id,
                                   "stat_type": stat_type, "value": value})
        loaded += 1
    print(f"player_stats: loaded {loaded} games this run")
    return loaded
```

  And in `main()`, add the `--sport` arg and thread it (keep `SPORT`/`SEASONS` module constants;
  `SPORT` becomes the CLI default):
```python
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=["mls", "ucl"], default=SPORT)
    parser.add_argument("--season", default="all")
    parser.add_argument("--only", choices=["fixtures", "stats", "all"], default="all")
    args = parser.parse_args()
    seasons = SEASONS if args.season == "all" else [int(args.season)]
    client = APISportsClient(args.sport)
    engine = db.get_engine()
    try:
        for season in seasons:
            finished = backfill_fixtures(client, engine, season, args.sport)
            if args.only in ("stats", "all"):
                backfill_player_stats(client, engine, finished, args.sport)
    except QuotaExhaustedError as e:
        print(f"Stopping: {e}\nRe-run later to resume — loaded games are skipped.")
```
  Update the module docstring's first line to say "MLS/UCL (soccer)" instead of "MLS (soccer)".

- [ ] **Step 4 (architect): run** `tests/test_ucl_wiring.py` + `tests/test_soccer_ingest.py`
  + `tests/test_ids.py` — expect PASS (existing MLS tests unchanged, offsets test still green;
  if `test_ids.py` enumerates sports and now misses `ucl`, add `ucl` to it).
- [ ] **Step 5 (architect): commit** `feat(ingestion): UCL config + soccer_backfill --sport param (§16)`.

---

### Task 2: UCL odds + builder maps (delegate: free)

**Files:** Modify `ingestion/odds_ingest.py`, `optimizer/builder.py`; append to `tests/test_ucl_wiring.py`.

**Interfaces (produced):** `STAT_MAPS['ucl']`, `GAME_MARKETS['ucl']` (reuses `full_game_total`),
`TEAM_MARKETS['ucl']`, `SLATE_WINDOW_DAYS['ucl']=0`, `_team_class('ucl')=='game_tier'`.

- [ ] **Step 1: Append failing tests** to `tests/test_ucl_wiring.py`:

```python
def test_ucl_stat_and_game_markets():
    from ingestion.odds_ingest import STAT_MAPS, GAME_MARKETS
    # UCL reuses the same soccer statIDs as MLS (same SGO soccer feed).
    assert STAT_MAPS["ucl"] == {
        "shots": "shots", "shots_onGoal": "shots_on_goal", "tackles": "tackles",
    }
    assert GAME_MARKETS["ucl"] == {"full_game_total": ("points", "all", "game")}


def test_ucl_builder_wiring():
    from optimizer.builder import TEAM_MARKETS, SLATE_WINDOW_DAYS, _team_class
    assert TEAM_MARKETS["ucl"] == ("full_game_total",)
    assert SLATE_WINDOW_DAYS["ucl"] == 0
    assert _team_class("ucl") == "game_tier"
```

- [ ] **Step 2 (architect): run — expect FAIL** (`KeyError 'ucl'`).

- [ ] **Step 3: Implement.** In `ingestion/odds_ingest.py`, add to `STAT_MAPS` AFTER the `mls`
  entry:
```python
    # UCL (soccer) — SAME SGO soccer statIDs as MLS (shared soccer feed).
    "ucl": {
        "shots": "shots",
        "shots_onGoal": "shots_on_goal",
        "tackles": "tackles",
    },
```
  and to `GAME_MARKETS` AFTER the `mls` entry:
```python
    # UCL match total goals — reuses full_game_total (points/ou/game), like MLS.
    "ucl": {
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
    "ucl": ("full_game_total",),
}
```
```python
SLATE_WINDOW_DAYS = {"mlb": 0, "nfl": 4, "nba": 0, "mls": 0, "ucl": 0}
```
```python
def _team_class(sport):
    """--team-only save class: NFL/NBA/MLS/UCL full-game markets save game_tier,
    distinct from MLB NRFI/F5 team_tier. Both kind=builder."""
    return "game_tier" if sport in ("nfl", "nba", "mls", "ucl") else "team_tier"
```

- [ ] **Step 4 (architect): run — expect PASS.**
- [ ] **Step 5 (architect): commit** `feat(builder): UCL odds/builder maps (§16)`.

---

### Task 3: UCL daily chain block (delegate: deepseek — control surface)

**Files:** Modify `scripts/daily_chain.sh`. No unit test; architect runs `bash -n` + semantics check.

Mirror `_mls_daily_build` exactly. Add `_ucl_daily_build` after it, wire best-effort after the
MLS block and before `_step settle`. Inert on free (no odds/games → 0 legs).

- [ ] **Step 1: Add the helper** after `_mls_daily_build`'s closing `}` (mirror it; single-TAB
  bodies, TAB+spaces continuations):
```bash
	# UCL (soccer) — DAILY like MLS. Same shared soccer maps, league 2. Best-effort:
	# a UCL failure logs but never aborts the MLB chain. Live only with a paid
	# API-Sports plan (current-season stats); inert on free.
	_ucl_daily_build() {
		_step_retry ucl_odds  "$PY" -m ingestion.odds_ingest --sport ucl &&
			_step ucl_builder_1.4 "$PY" -m optimizer.builder --sport ucl --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step ucl_builder_2.0 "$PY" -m optimizer.builder --sport ucl --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step ucl_game_1.4    "$PY" -m optimizer.builder --sport ucl --team-only --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step ucl_game_2.0    "$PY" -m optimizer.builder --sport ucl --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save
	}
```

- [ ] **Step 2: Wire in** — after the `mls_scores` line and before `_step settle`, insert (mirror
  the MLS best-effort wrappers; UCL score refresh is `soccer_backfill --sport ucl --season 2024
  --only fixtures`, paid-gated so best-effort/non-fatal; `--season 2024` is the same
  free/structural-era placeholder as MLS — architect swaps to the live season with the paid plan):
```bash
		{ _ucl_daily_build || echo "=== ucl daily build: FAILED (non-fatal, MLB chain continues) ==="; } &&
		{ _step_retry ucl_scores "$PY" -m ingestion.soccer_backfill --sport ucl --season 2024 --only fixtures || echo "=== ucl_scores: FAILED (non-fatal) ==="; } &&
```

- [ ] **Step 3 (architect): `bash -n scripts/daily_chain.sh`** → no output.
- [ ] **Step 4 (architect): semantics check** — forced UCL failure still lets `settle` run;
  MLS block precedes UCL; UCL precedes settle.
- [ ] **Step 5 (architect): commit** `feat(chain): UCL daily build + settle, best-effort (§16)`.

---

### Task 4: dashboard UCL tab (delegate: free)

**Files:** Modify `web/app/builder/SportTabs.tsx`, `page.tsx`, `web/app/lib/teamNames.ts`.
Read `web/AGENTS.md` first (Next 16). MLB/NFL/NBA/MLS byte-unchanged.

- [ ] **Step 1: `SportTabs.tsx`** — add `{ key: "ucl", label: "UCL" }` after the `mls` entry.

- [ ] **Step 2: `page.tsx`** — add the `ucl` key to `SPORT_CFG` after `mls`:
```tsx
  ucl: {
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
      title: "No Champions League parlays yet",
      body: "UCL parlays build on matchdays during the season once live odds + settlement data are connected.",
    },
  },
```
  and widen the sport union + resolution (currently `"mlb" | "nfl" | "nba" | "mls"`):
```tsx
  const sport: "mlb" | "nfl" | "nba" | "mls" | "ucl" =
    sportParam === "nfl" ? "nfl" : sportParam === "nba" ? "nba" : sportParam === "mls" ? "mls" : sportParam === "ucl" ? "ucl" : "mlb";
```

- [ ] **Step 3: `teamNames.ts`** — UCL clubs rotate season-to-season and number in the dozens
  (incl. qualifiers). A full nickname table is impractical and the map already falls back to the
  full name for anything unmapped (never wrong). So add ONLY a handful of the marquee clubs whose
  full DB names are long, appended after the MLS block, and update the leading comment to note
  UCL uses mostly full-name fallback:
```ts
  // UCL (partial — most clubs fall back to full names, which are already short):
  "Paris Saint Germain": "PSG",
  "Manchester City": "Man City",
  "Manchester United": "Man Utd",
  "Bayern München": "Bayern",
  "Borussia Dortmund": "Dortmund",
  "Atletico Madrid": "Atlético",
  "Inter": "Inter",
```
  (Exact DB names are whatever API-Sports returns; the architect reconciles these against the
  loaded `teams` rows in verification and drops/renames any that don't match — a miss is a
  harmless full-name fallback, not a bug.)

- [ ] **Step 4 (architect): `cd web && npx tsc --noEmit` + `npm run build`** → clean.
- [ ] **Step 5 (architect): commit** `feat(web): UCL builder tab + nicknames (§16)`.

---

### Task 5: LIVE backfill + verification + landing (ARCHITECT ONLY)

- [ ] **Step 1: LIVE backfill** (reserved lane — new empty +400M ID space):
  `soccer_backfill --sport ucl --season 2022/2023/2024 --only fixtures` (3 calls → all fixtures +
  scores), then `--only stats` accretes a player-stats sample resumably against the 100/day cap
  (like MLS #1). Verify: row counts, a spot-check game (goals match a known result), 0 FK orphans,
  idempotent re-run, MLB/NBA/NFL/MLS counts unchanged.
- [ ] **Step 2: SGO UCL `--dry-run`** — `odds_ingest --sport ucl --dry-run`. NOTE UCL may be
  between rounds in early August (group stage ~mid-Sept), so the live feed may carry few/no UCL
  events; the soccer statIDs are already pinned by the MLS dry-run (same SGO soccer feed), so a
  thin UCL feed is acceptable — record what the feed shows.
- [ ] **Step 3: structural builder run** — `builder --sport ucl` (player) + `--team-only` run
  clean (0 legs — no UCL odds/current games; no crash).
- [ ] **Step 4: settlement on real UCL stats** — add a regression test to `tests/test_ucl_wiring.py`
  embedding a real loaded 2022–24 UCL match total (`game_total`) + a real player shots value,
  asserting `settle_leg`/`leg_status` score them (pure, mirror MLS `test_soccer_settlement_on_real_2024_stats`).
- [ ] **Step 5: isolation** — MLB/NBA/NFL/MLS builder byte-behavior unchanged; Budgerr
  `saved`/`record` unchanged; full pytest green.
- [ ] **Step 6: reconcile Task-4 nicknames** against the loaded UCL `teams` names; drop/fix misses.
- [ ] **Step 7: land** — `graphify update .`; README §16.4 UCL BUILT; push; API kickstart
  (`optimizer/builder.py` is API-imported). Dashboard UCL-tab eyeball via login-disabled dev
  preview (`SESSION_SECRET=` empty). Update dashboard visual proof.

## Self-Review
- **Spec coverage:** config +400M ✓ (T1); backfill `--sport` param, MLS-default byte-safe ✓ (T1);
  STAT_MAPS/GAME_MARKETS/TEAM_MARKETS/window/_team_class ✓ (T2); settle NO-CHANGE (soccer AET/PEN
  already in `_FINAL_GAME_STATUSES`) ✓; chain ✓ (T3); dashboard + nicknames ✓ (T4); live backfill +
  structural + settlement verification ✓ (T5); MLB/NBA/NFL/MLS byte-unchanged ✓; Budgerr-safe ✓.
- **Placeholder scan:** none — the chain `--season 2024` placeholder + partial UCL nickname set are
  explicit architect-review items (paid-era token; full-name fallback), not code gaps.
- **Type consistency:** `sport` param default `"mls"` preserves the 3-arg `backfill_fixtures`
  signature the existing `tests/test_soccer_ingest.py` calls; `full_game_total` reused verbatim;
  `ucl` stat_type values match the shared `extract_soccer_player_stats` output.
