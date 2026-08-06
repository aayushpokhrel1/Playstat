# NHL — New Sport (`sport='nhl'`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or
> superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **Execution note:** mechanical edits are delegated to cheap workers
> (`~/.claude/bin/delegate free`, `deepseek` fallback) that read/edit files but **cannot run
> shell/tests/git**. The architect runs graphify, pytest, `tsc`/`next build`, the LIVE
> backfill, live verification, `launchctl kickstart`, and all commits.
>
> **graphify before reading source** (`graphify query "<q>"`, `graphify-out/graph.json`
> exists) — repo rule. In a worktree the graph is absent (gitignored); reading source
> directly is expected there — don't burn turns failing the rule.

**Goal:** Add the NHL as a new sport (`sport='nhl'`) the way NBA/MLS/UCL were built — land
its teams/games/final-scores + player box scores from **NHL's own free public API**
(`api-web.nhle.com`), wire its free SportsGameOdds feed (`odds_league_id="NHL"`), and add the
builder maps / chain block / dashboard tab — so NHL match-total (goals) + player-prop
(shots-on-goal, saves) parlays build/settle/report exactly like the other sports.

**The headline difference from NBA/MLS/UCL: NHL is LIVE-FOR-FREE.** Both its odds (SGO free
tier) AND its current-season stats (NHL's own API) are free + current, so unlike the
paid-gated NBA/MLS/UCL tracks, NHL flips fully live for **$0** when the season opens (~Oct
2026). No paid API-Sports plan. (README §16.4 "NEXT TRACK: NHL".)

## First-hour gate — PASSED (architect, 2026-08-06)
`api-web.nhle.com` confirmed **free (no auth, HTTP 200), current-season (2025-26), per-player
box scores + final scores**:
- `GET /v1/score/{YYYY-MM-DD}` → games with `id`, `gameState`, `homeTeam/awayTeam.{abbrev,score}`.
  (2026-01-15 returned 10 final games, e.g. `2025020740` MTL 3 @ BUF 5.)
- `GET /v1/gamecenter/{gameId}/boxscore` → `playerByGameStats.{home,away}Team.{forwards,defense,goalies}`.
  Skaters carry `sog` (shots-on-goal), `goals`, `assists`, `points`, `hits`, `blockedShots`,
  `pim`, `toi`. Goalies carry `saves`, `shotsAgainst`, `goalsAgainst`. (Fowler 22 saves,
  Caufield 5 SOG spot-verified.) `gameOutcome.lastPeriodType` ∈ {REG,OT,SO}.
- `GET /v1/standings/now` (307 → follow redirect) → all 32 teams (used only to cross-check the
  nickname map; **the backfill sources team names from the schedule feed**, see Task 1).

**SGO NHL feed — LIVE-PROBED (architect, 2026-08-06, offseason):** `get_events("NHL",
odds_available=True)` → 35 events. Game-level markets present NOW: **`('points','ou','game')`
= 62 odds → `full_game_total` (match-total goals) is priced and confirmed.** Also present but
**SKIPPED**: `('points','ml3way','reg')` (3-way moneyline — hockey has regulation ties; does
not fit two-sided geometry, same call as soccer), `('points','ou','1p')` (1st-period total),
`bothTeamsScored` (yn). **No player props in the offseason feed** (`event.players` empty) —
sog/saves statIDs MUST be `--dry-run`-pinned at preseason (first event 2026-09-29), exactly
the NFL/NBA/MLS precedent.

## Architecture
Mirror NBA (own daily cadence, game-market total + player props, `team_game_stats('points')`
= the sport's scoring unit) but with a **new ingestion module** because NHL's API shape
differs from API-Sports/StatsAPI. Reuse everything downstream: `full_game_total` market name
(zero new geometry/settlement plumbing), the sport-parameterized `odds_ingest`/`builder`/
`settle`/web builder page.

**`modeling/settle.py` needs NO change.** `nhl_backfill` stores every final NHL game
(REG/OT/SO) as `status='FT'`, and the match total (goals, incl. OT/shootout, in the official
final score) is written to `team_game_stats('points')` — which `settle_builder_parlays`
already sums for `full_game_total` via the existing `{FT,AOT,AET,PEN}` `_FINAL_GAME_STATUSES`.
(SO caveat noted in Task 1.)

## Tech Stack
Python 3.11 (`/Users/aayushpokhrel/dev/playstat/.venv`), Postgres (**LIVE — no test DB**),
pytest, Next 16 (`web/` — READ `web/node_modules/next/dist/docs/` before writing Next code;
`middleware.ts` is deprecated → `proxy.ts`), SportsGameOdds (`odds_league_id="NHL"`, free),
`api-web.nhle.com` (free, key-less).

## Global Constraints
- **§15.8 guardrails BINDING**: rank on devig market prob, `market_prob ≥ 0.55`, 2–4 legs,
  across-game only, paper-only, **no +EV / edge / green language**.
- **No live DB in tests** — pure functions or the fake-engine pattern
  (`tests/test_ucl_wiring.py` / `tests/test_soccer_ingest.py`). `ingestion.db.get_engine()`
  is LIVE; no test may call it.
- **Additive-only; MLB/NBA/NFL/MLS/UCL byte-unchanged.** Every NHL addition is a new map key
  / branch / default-valued param / new module. No existing signature changes value for
  existing sports. `nhl` is a new `sport` value **Budgerr never requests** (Budgerr default
  is `mlb`; it reads `/parlay-builder/saved`, not the live search) → Budgerr-safe.
- **README §16.4 updated + pushed in the landing commit(s).**
- **API kickstart after landing**: `optimizer/builder.py` **is** API-imported (the
  `TEAM_MARKETS`/`SLATE_WINDOW_DAYS`/`_team_class` edit); `modeling/settle.py` is too (no NHL
  edit, but kickstart anyway per policy). `ingestion/*` is not API-imported. Architect runs
  `launchctl kickstart -k gui/$(id -u)/com.playstat.api`.

## The ID scheme (highest-stakes decision — architect-decided, 2026-08-06)
**Facts:** `games.game_id` / `players.player_id` / `teams.team_id` are **INTEGER (INT4, max
2,147,483,647)** — NOT bigint (verified: every FK in `db/migrations/00*.sql` is `INTEGER
REFERENCES …(…_id)`). NHL **native game ids are ~2.03 billion** (`2025020740` =
`season*1e6 + gametype*1e4 + gamenum`), which already sit *above every existing band*
(NBA 0, MLB 100M, NFL's REAL 400–420M span per §11, MLS 300M+, UCL 500M+) and *fit INT4*
(2.025e9 < 2.147e9) but leave **no room to add a positive offset** — `offset + 2.03e9`
overflows INT4. So the naive "+600M offset on the raw id" is impossible here.

**Decision — `id_offset = 1_000_000_000` (1B), applied uniformly, with a game-id epoch
reduction:**
```
NHL_ID_OFFSET       = 1_000_000_000
NHL_GAME_ID_EPOCH   = 2_000_000_000   # NHL native ids embed the season at the 1e9 place
team_id   = NHL_ID_OFFSET + raw_team_id            # raw 1..68   -> 1.000000001e9 ..
player_id = NHL_ID_OFFSET + raw_player_id          # raw ~7-digit-> ~1.008e9
game_id   = NHL_ID_OFFSET + (raw_game_id - NHL_GAME_ID_EPOCH)   # 2025020740 -> 1_025_020_740
# refetch the boxscore: raw_game_id = game_id - NHL_ID_OFFSET + NHL_GAME_ID_EPOCH
#                                    = game_id + 1_000_000_000   (== stored + 1B)
```
Assert `raw_game_id >= NHL_GAME_ID_EPOCH` (guards pre-2000 ids we never ingest).

**Why 1B is correct (range-check, per §11's mandate):**
- NHL game_ids land in **[1.023e9, 1.100e9)** for seasons 2023–2099 (raw 2.023e9–2.099e9 − 1e9).
  All `< 2,147,483,647` (INT4) with >1.0e9 headroom, and `>` NFL's real ceiling (200M +
  2099·1e5 = 409.9M), MLS/UCL (500M + realistic fixture ids), everything.
- NHL team/player ids land in **[1.0e9, 1.1e9)** — same clearance.
- 1B is unreachable by the soccer tracks' unbounded `offset + raw_fixture` growth for
  centuries (UCL reaches 1B only when the API-Sports fixture counter hits 500M; it is ~1.37M
  today). 1B maximizes separation from everything below *while* keeping NHL's own growing
  game_ids under INT4 (a higher offset would eat that headroom).
- **Guard test** `tests/test_nhl_wiring.py::test_nhl_ids_clear_all_bands_and_fit_int4`
  (mirror `test_ucl_wiring.test_ucl_offset_clears_nfls_real_game_id_band`): asserts
  `nhl_game_id(2023020001) > 410_000_000` and `> 501_000_000` and `< 2_147_483_647`, and
  round-trips `raw -> game_id -> raw`.

## File Structure
- `ingestion/config.py` — `SPORTS['nhl']` (`id_offset` 1_000_000_000, `odds_league_id`
  `"NHL"`; NHL's own API is key-less so no `base_url` needed — comment it). (Task 1)
- `ingestion/nhl_backfill.py` — **CREATE**. NHL's own-API backfill (mirror
  `ingestion/mlb_backfill.py`'s structure: `requests.Session`, polite pacing, retries,
  `db.upsert`, `--only teams|games|stats|all`, resumable player stats via
  `db.game_ids_with_stats`). (Task 1)
- `ingestion/odds_ingest.py` — `STAT_MAPS['nhl']` + `GAME_MARKETS['nhl']`. (Task 2)
- `optimizer/builder.py` — `TEAM_MARKETS['nhl']`, `SLATE_WINDOW_DAYS['nhl']=0`,
  `_team_class` += `nhl`. (Task 2)
- `scripts/daily_chain.sh` — `_nhl_daily_build` best-effort block, wired before `settle`. (Task 3)
- `web/app/builder/SportTabs.tsx`, `web/app/builder/page.tsx`,
  `web/app/api/builder-search/route.ts`, `web/app/lib/teamNames.ts` — NHL tab/config/union/
  nicknames. (Task 4)
- `tests/test_nhl_wiring.py` — **CREATE**: pure map tests, id-scheme guard, box-score
  extractor tests (fake-engine/pure). (Tasks 1–2)

---

### Task 1 — config + `nhl_backfill.py` (delegate: deepseek — the new module)

**Files:** Modify `ingestion/config.py`; **Create** `ingestion/nhl_backfill.py`; start
`tests/test_nhl_wiring.py`.

**`ingestion/config.py`** — add to `SPORTS`, mirroring the existing entries' comment style:
```python
    # NHL (hockey) — NHL's OWN free public API (api-web.nhle.com, key-less), NOT
    # API-Sports: their hockey API's current season is paid, NHL's own is free +
    # current (the MLB StatsAPI pattern). Odds via SGO free tier (odds_league_id
    # "NHL"). Ingested by ingestion/nhl_backfill.py.
    # id_offset is +1B, NOT the next +100M band: NHL native game ids are ~2.03e9
    # (season*1e6+...), already above every band and fitting INT4 (2.147e9) with no
    # room for a positive offset. nhl_backfill stores game_id = 1e9 + (raw - 2e9)
    # (see NHL_GAME_ID_EPOCH there); teams/players are 1e9 + raw. 1B clears NFL's
    # real 400-420M span + UCL 500M and stays under INT4. See README §11 / §16.4.
    "nhl": {
        "odds_league_id": "NHL",
        "id_offset": 1_000_000_000,
    },
```

**`ingestion/nhl_backfill.py`** — CREATE, structurally mirroring `ingestion/mlb_backfill.py`
(read it first). Key specifics:
- Module constants: `NHL_API_BASE = "https://api-web.nhle.com/v1"`,
  `NHL_ID_OFFSET = SPORTS["nhl"]["id_offset"]`, `NHL_GAME_ID_EPOCH = 2_000_000_000`,
  `SECONDS_BETWEEN_REQUESTS = 0.3`, retries like mlb.
- **A `requests.Session` client** with polite pacing + 5xx/timeout retries (copy
  `MLBStatsClient` almost verbatim; NHL API is key-less). Some endpoints 307-redirect
  (`standings/now`) — use `allow_redirects=True` (requests default). Send a
  `User-Agent` header (the API 307s/blocks a bare urllib UA in some cases).
- **Pure id helpers (unit-tested, no I/O):**
  ```python
  def nhl_team_id(raw):   return NHL_ID_OFFSET + int(raw)
  def nhl_player_id(raw): return NHL_ID_OFFSET + int(raw)
  def nhl_game_id(raw):
      raw = int(raw)
      assert raw >= NHL_GAME_ID_EPOCH, f"pre-2000 NHL id not supported: {raw}"
      return NHL_ID_OFFSET + (raw - NHL_GAME_ID_EPOCH)
  def nhl_raw_game_id(game_id):  # inverse, for boxscore refetch
      return int(game_id) - NHL_ID_OFFSET + NHL_GAME_ID_EPOCH
  ```
- **`extract_skater_stats(skater)` / `extract_goalie_stats(goalie)` (pure, unit-tested):**
  long-format dicts from a boxscore player entry. Skater →
  `{"shots_on_goal": sog, "goals": goals, "assists": assists, "points": points,
    "hits": hits, "blocked_shots": blockedShots, "pim": pim}`. Goalie →
  `{"saves": saves, "shots_against": shotsAgainst, "goals_against": goalsAgainst}`.
  Store the richer set (cheap — it's all in the boxscore); `STAT_MAPS['nhl']` maps only the
  SGO-priced subset (`shots_on_goal`, `saves`) so props stay settleable. Zeros are real
  outcomes — keep them. Skip a goalie who never entered (`toi == "00:00"` and 0 shots
  against) the way mlb skips bench players.
- **`final_status(game)`**: NHL `gameState` for a finished game is `"OFF"` or `"FINAL"`
  (official) — store `"FT"` for either, else the raw `gameState`. **All finals (REG/OT/SO)
  → `"FT"`** — no distinct OT/SO status (the total already includes OT/shootout goals in the
  official score, so `full_game_total` settles correctly; `_FINAL_GAME_STATUSES` needs no
  change). **SO caveat to document in the docstring:** a shootout adds the decider goal to
  the winner's official score; most books settle NHL totals on the official final incl. the
  SO goal, but a minority exclude it — a ±1 near-line edge case on ~5–8% of games. We settle
  on the official final (what the NHL API reports). Flag for re-check once live settled lines
  exist; do not block.
- **`backfill_games(client, engine, ...)`** — enumerate the target season by walking the
  score feed day-by-day: `GET /score/{date}` returns `{games:[...], nextDate, currentDate}`;
  each game has `id`, `gameState`, `gameType` (2=regular, 3=playoff), `homeTeam`/`awayTeam`
  with `id`, `abbrev`, `placeName.default`, `commonName.default`, `score`. Walk `nextDate`
  from the season start date through the end date.
  - **Upsert teams from the game feed's team blocks** (every team recurs across the season —
    no separate teams endpoint needed, and the score/schedule feed's `placeName` is clean for
    the NY teams, unlike `standings`). `teams.name = f"{placeName} {commonName}"` (e.g.
    "New York Rangers", "Buffalo Sabres", "Montréal Canadiens", "St. Louis Blues"), `sport`
    `"nhl"`, `team_id = nhl_team_id(raw_id)`. `conference` may be left NULL (no clean
    conference in this feed) or set from a small ABBREV→conference map (optional; NULL is
    fine — matches NBA-era nullable column).
  - Upsert each game: `game_id = nhl_game_id(id)`, `sport "nhl"`, `date` = the feed's local
    game date (`currentDate` of that score page, or the game's own date field — use the
    home-local date), `home_team_id`/`away_team_id` = `nhl_team_id(...)`, `status =
    final_status(game)`.
  - For **final** games, write **each team's goals** to `team_game_stats` as
    `stat_type='points'` (`value` = that side's `score`) — this is the match-total unit
    `full_game_total` settles against (mirrors NBA `points` / soccer goals-as-`points`).
  - Return the list of finished games (with their raw ids) for the stats pass.
- **`backfill_player_stats(client, engine, finished_games)`** — resumable exactly like mlb:
  `already = db.game_ids_with_stats(conn)`; for each finished game not already loaded,
  `GET /gamecenter/{raw_id}/boxscore`, upsert players (id, name from `name.default`,
  `team_id`, `position`) + their long-format stat rows. `name.default` in the boxscore is
  abbreviated ("Z. Benson"); prefer `firstName.default + " " + lastName.default` if present
  on the entry, else fall back to `name.default`. (Delegate: check the boxscore player entry
  for `firstName`/`lastName`; the architect's probe saw only `name.default` — use whichever
  the live payload has, preferring the full name.)
- **`main()` / CLI**: `--only {teams,games,stats,all}` (default `all`), and a season selector.
  NHL "season" is the start year (e.g. `2025` for 2025-26). Provide `--season` (default = the
  current/most-recent season) → derive a date range (`{season}-09-15` .. `{season+1}-06-30`
  covers preseason→playoffs) walked via the score feed. A `current_nhl_season()` sentinel
  (like NBA's `current_nba_season()`) so the daily live pull targets the live season without a
  hardcoded year: NHL season N runs Sep(N)–Jun(N+1), so `current_nhl_season()` = this year if
  month >= 9 else last year. Keep it simple and pure.

**Tests (`tests/test_nhl_wiring.py`, pure — no DB):**
- `test_nhl_ids_clear_all_bands_and_fit_int4` (the guard, see ID scheme above).
- `test_nhl_game_id_roundtrip` (`nhl_raw_game_id(nhl_game_id(x)) == x`).
- `test_extract_skater_stats` / `test_extract_goalie_stats` on a captured boxscore fragment
  (assert `shots_on_goal`/`saves` extracted, zeros kept, bench goalie skipped).
- `test_final_status` (OFF/FINAL → FT; LIVE/FUT → passthrough).
- `test_team_name_from_feed` (place+common concat, incl. a NY team → "New York Rangers").

**Acceptance:** `.venv/bin/python -m pytest tests/test_nhl_wiring.py -q` green (architect runs);
`python -m ingestion.nhl_backfill --only teams --season 2025` importable/`--help` works
(architect smoke-runs a tiny slice against LIVE — reserved lane).

---

### Task 2 — odds/builder maps + guard tests (delegate: free)

**Files:** Modify `ingestion/odds_ingest.py`, `optimizer/builder.py`; extend
`tests/test_nhl_wiring.py`.

- `ingestion/odds_ingest.py`:
  ```python
  # STAT_MAPS: NHL player props. Game total is via GAME_MARKETS below. sog/saves
  # statIDs are the architect's best-known SGO hockey names — PRESEASON-VERIFY via
  # `odds_ingest --sport nhl --dry-run` (no player props in the offseason feed as of
  # 2026-08-06). Values MUST match ingestion/nhl_backfill stat_types (settleable).
  "nhl": {
      "shots_onGoal": "shots_on_goal",
      "saves": "saves",
  },
  # GAME_MARKETS: NHL match-total goals — reuses full_game_total (points/all/game),
  # LIVE-CONFIRMED 2026-08-06 (('points','ou','game') priced). Skip ml3way/1p/BTS.
  "nhl": {
      "full_game_total": ("points", "all", "game"),
  },
  ```
  (`odds_ingest`'s `--sport` choices are `list(STAT_MAPS)`, so `--sport nhl` auto-enables.)
- `optimizer/builder.py`: `TEAM_MARKETS['nhl'] = ("full_game_total",)`;
  `SLATE_WINDOW_DAYS['nhl'] = 0`; add `"nhl"` to `_team_class`'s game_tier tuple.
  (`builder.py --sport` is free-form `default="mlb"`, no choices list — no other edit.)
- Tests: `test_nhl_maps_present` (STAT_MAPS/GAME_MARKETS/TEAM_MARKETS/SLATE_WINDOW_DAYS have
  `nhl`; `_team_class("nhl") == "game_tier"`); `test_nhl_stat_map_targets_are_backfill_stats`
  (STAT_MAP values ⊆ the stat_types `nhl_backfill` emits — catches a settle mismatch).

**Acceptance:** full `pytest -q` green; `odds_ingest --sport nhl --dry-run` and
`builder --sport nhl` are architect-run live (Task 5).

---

### Task 3 — daily chain block (delegate: deepseek — control surface)

**File:** `scripts/daily_chain.sh`. Add `_nhl_daily_build` mirroring `_nba_daily_build` /
`_mls_daily_build` / `_ucl_daily_build` (read them first — best-effort, non-fatal, `set +e`
around the block, logs, never aborts the chain). Wire it **after the other best-effort sport
builds and before `settle`**. It should: refresh the current NHL season's finals+scores
(`nhl_backfill --only games --season current`), pull NHL odds
(`odds_ingest --sport nhl`), and run the NHL builder (player + `--team-only`) with `--save`.
**Live-for-free**: unlike the soccer/NBA blocks (inert until a paid plan), this block does
real work the moment the season opens — but stays harmless in the offseason (0 games → 0
legs, no crash). `bash -n scripts/daily_chain.sh` must stay clean.

**Acceptance:** `bash -n scripts/daily_chain.sh` clean (architect); architect dry-eyeballs the
block ordering and the `--season current` plumbing.

---

### Task 4 — dashboard tab + 32 nicknames (delegate: free)

**Files:** `web/app/builder/SportTabs.tsx`, `web/app/builder/page.tsx`,
`web/app/api/builder-search/route.ts`, `web/app/lib/teamNames.ts`. READ
`web/node_modules/next/dist/docs/` before writing Next code; match `web/app/edges/`
conventions + DESIGN.md (near-black surface, one signal-green accent, Geist).

- `SportTabs.tsx`: add `{ key: "nhl", label: "NHL" }` to the `SPORTS` array.
- `page.tsx`: add `nhl` to `SPORT_CFG` (mirror `nba`: `tier2:"game"`, "Tonight's low-risk
  parlays", "Game-market parlays" heading, a match-total-style `tier2Note`/`tier2Empty`, and
  an honest `emptyAll` — "NHL parlays build nightly once the season opens (~October). Check
  back then."). Extend the sport **union type** on `page.tsx:93` to include `"nhl"` and the
  ternary on `:94` to route `sportParam === "nhl" ? "nhl"`.
- `web/app/api/builder-search/route.ts:22` — the live-Build sport whitelist is currently
  `sportRaw === "nfl" || sportRaw === "nba" ? sportRaw : "mlb"`. **This already drops mls/ucl
  (a latent bug: clicking Build on those tabs returns MLB parlays).** Fix additively to accept
  the full known set incl. nhl, e.g. `["nfl","nba","mls","ucl","nhl"].includes(sportRaw) ?
  sportRaw : "mlb"`. (Budgerr-safe: default still mlb; Budgerr reads `saved`, not this route.)
- `web/app/lib/teamNames.ts`: add a **full 32-team NHL nickname map** keyed on the EXACT
  DB-stored full name (`placeName + " " + commonName` from the schedule feed) → nickname.
  **Accents/punctuation MUST match exactly** ("Montréal Canadiens", "St. Louis Blues"). The
  canonical list (architect-verified against `/v1/schedule` + `/v1/standings`, 2026-08-06):
  ```
  Anaheim Ducks→Ducks; Boston Bruins→Bruins; Buffalo Sabres→Sabres;
  Calgary Flames→Flames; Carolina Hurricanes→Hurricanes; Chicago Blackhawks→Blackhawks;
  Colorado Avalanche→Avalanche; Columbus Blue Jackets→Blue Jackets; Dallas Stars→Stars;
  Detroit Red Wings→Red Wings; Edmonton Oilers→Oilers; Florida Panthers→Panthers;
  Los Angeles Kings→Kings; Minnesota Wild→Wild; Montréal Canadiens→Canadiens;
  Nashville Predators→Predators; New Jersey Devils→Devils; New York Islanders→Islanders;
  New York Rangers→Rangers; Ottawa Senators→Senators; Philadelphia Flyers→Flyers;
  Pittsburgh Penguins→Penguins; San Jose Sharks→Sharks; Seattle Kraken→Kraken;
  St. Louis Blues→Blues; Tampa Bay Lightning→Lightning; Toronto Maple Leafs→Maple Leafs;
  Utah Mammoth→Mammoth; Vancouver Canucks→Canucks; Vegas Golden Knights→Golden Knights;
  Washington Capitals→Capitals; Winnipeg Jets→Jets
  ```
  (A **fixed 32-team league**, so a full map is worth it — unlike UCL's rotating clubs.
  Match whatever container `teamNames.ts` uses per sport; follow its existing shape.)

**Acceptance:** `cd web && npx tsc --noEmit` (or `next build`) clean (architect);
architect eyeballs all 4 tabs' honest offseason empty states via login-disabled dev
(`SESSION_SECRET=` empty — dashboard-visual-preview-noauth memory).

---

### Task 5 — architect-only: live backfill, verify, land (RESERVED LANE)
Not delegated. In order:
1. `pytest -q` full suite green (expect +N NHL tests on the existing 382).
2. **LIVE backfill a small verified slice first** into the empty 1B band: one recent finished
   date (`nhl_backfill --only games` for a ~1-week window, then `--only stats` for a few
   games). Verify: team/game/`team_game_stats('points')` counts; a spot game (MTL 3 @ BUF 5,
   goals total 8); 0 FK orphans; **0 cross-sport game_id collisions** (`SELECT game_id,
   count(*) FROM games GROUP BY game_id HAVING count(*)>1` empty); idempotent re-run adds 0.
   Then the **full most-recent season** backfill.
3. `odds_ingest --sport nhl --dry-run` against the LIVE SGO feed — confirm `('points','ou',
   'game')` maps to `full_game_total`; **pin the sog/saves player statIDs if player props are
   open** (preseason ~late Sep); if still offseason, leave the PRESEASON-VERIFY note and
   record that game-total is confirmed.
4. `builder --sport nhl` (player) + `--team-only` run clean (0 legs offseason is fine).
5. Spot-settle a REAL backfilled final through the reused `settle_leg`/`game_total` path (a
   match-total leg on a known final) to prove the soccer-style `points` settlement works for
   NHL with no settle change.
6. `launchctl kickstart -k gui/$(id -u)/com.playstat.api`; confirm live API still 200s and
   MLB/NBA/NFL/MLS/UCL builder responses byte-unchanged (additive check).
7. Browser-verify the NHL tab (login-disabled dev).
8. **Update README §16.4** (NHL BUILT & VERIFIED entry: id scheme, backfill counts, what's
   confirmed live vs preseason-pending, test count) **in the landing commit**; push.

## Delegation order & review
Commit THIS plan first (worktrees branch from HEAD). Then, on clean trees:
Task 1 (deepseek) → review diff (id helpers, extractors, FT mapping, offset math) → Task 2
(free) → Task 3 (deepseek) → Task 4 (free). Review the **actual `git diff`**, never the
worker's report. Run pytest + tsc yourself. Then Task 5 (architect). Put "graphify query
before reading source (or read directly in a worktree — graph is gitignored there)" in EVERY
delegate prompt.
