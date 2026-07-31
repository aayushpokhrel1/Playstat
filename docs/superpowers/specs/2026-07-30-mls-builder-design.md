# MLS Builder — Design (2026-07-30)

**Status:** DESIGNED, user-confirmed 2026-07-30 (separate `sport='mls'` key; 2-sub-project
split, #1 ingestion first; build on free 2022–2024 data). Ready for `writing-plans` (#1).

**Strategic frame:** README §16 roadmap item 4 — MLS is the first soccer sport. The builder
core, settlement, chain, dashboard, and record are already sport-parameterized (NBA/NFL
tracks). Soccer's new work is a **whole new stats provider** (API-Sports *football*) — hence
bigger than NBA (which reused existing basketball ingestion) and decomposed like NFL.

**No soccer model** — the builder ranks on SGO de-vigged market probability (§15.8 / §16). §15.8
guardrails BINDING (rank on devig market prob, `market_prob ≥ 0.55`, 2–4 legs, across-game only,
paper-only, no +EV/edge/green language).

---

## The paid-gate reality (why "build now, live later")
- **Odds are free** (SGO free tier covers MLS — live-probed 2026-07-30: `leagueID="MLS"`, player
  props shots/shots_onGoal/tackles, game market match-total `points`/`ou`/`game`).
- **Current-season settlement data is PAID.** API-Sports (our stats provider — one key reaches
  both basketball and football, config.py:7) **free tier caps at seasons 2022–2024**; 2025/26
  returns *"Free plans do not have access to this season"* (same gate NBA hits, §11).
- **Decision (user-confirmed):** build + **structurally verify the full pipeline on free 2022–
  2024 data** (three MLS seasons available: ~489/526/526 fixtures), exactly how NBA was built on
  2023-24. **Live settlement flips on the moment a paid API-Sports plan is added** — a later user
  cost decision (one account covers NBA + soccer). This spec assumes free/historical build.

## Probe-confirmed facts (live, 2026-07-30)
- **API-Sports football:** `https://v3.football.api-sports.io`, header `x-apisports-key`
  (same key as basketball). Free plan active, 100 req/day. MLS = league **253**; seasons
  **2022, 2023, 2024** accessible (2021/2025/2026 blocked). Endpoints: `/fixtures?league=253&
  season=YYYY` (fixtures + `goals.home`/`goals.away` + `fixture.status.short`), `/fixtures/
  players?fixture=ID` (per-player nested stats). `status.short` finals: `FT` (also `AET`/`PEN`
  for extra-time/penalties — treat all three as final).
- **Player-stat field structure** (`statistics[0]`): `shots.total`, `shots.on`,
  `tackles.total`, `passes.total`, `goals.total`/`goals.assists`/`goals.saves`, `games.minutes`.
- **Settlement mapping** (SGO statID → API-Sports football field):
  | SGO statID | API-Sports field | leg kind |
  |---|---|---|
  | `shots` | `shots.total` | player |
  | `shots_onGoal` | `shots.on` | player |
  | `tackles` | `tackles.total` | player |
  | match total (`points`/`ou`/`game`) | fixture `goals.home + goals.away` | game (team) |

## Sport-key model (user-confirmed)
- **Separate `sport='mls'`** now, `sport='ucl'` later. Zero schema change; reuses the
  sport-parameterized builder/settlement/chain/dashboard/record.
- **ID offset `mls` +300_000_000** (nba 0, mlb 100M, nfl 200M; `ucl` +400M later). API-Sports
  football fixture/team/player IDs are global but a given entity plays one competition, so no
  cross-league PK collision in practice.
- **Shared soccer market maps:** define the soccer stat/market dicts once (e.g. `_SOCCER_STAT_MAP`,
  `_SOCCER_GAME_MARKETS`) and reference from the `mls` (and later `ucl`) keys — no duplication.

## Markets — v1
- **Player props:** `shots`, `shots_onGoal`, `tackles` (two-sided O/U; starters over 0.5/1.5
  shots and defenders/mids over tackle lines clear 0.55). Goalscorer / `firstToScore` /
  `bothTeamsScored` are longshots the floor correctly excludes — not mapped.
- **Game market:** **match total goals** only (`points`/`ou`/`game`) — over 1.5/2.5 prices as a
  favorite. Saved as `class='game_tier'`.
- **SKIP `ml3way`** (3-way home/draw/away) — does not fit the two-sided `MARKET_GEOMETRY`
  (ou/homeaway); adding 3-way support is deferred. Also skip `sp` (Asian handicap) and 2-way
  `ml` for v1 (add later if wanted). Match total alone is a valid game tier (cf. NBA).

---

## Decomposition — 2 sub-projects, #1 first

### MLS #1 — Soccer ingestion (the new-provider data layer)
Independently testable: MLS 2022–2024 games/players/stats/final-scores land in the DB.

- **`ingestion/config.py`:** `SPORTS['mls']` = `{base_url:"https://v3.football.api-sports.io",
  league_id:253, odds_league_id:"MLS", id_offset:300_000_000}`. Note the football API uses the
  `x-apisports-key` header + `?season=` + `/fixtures` (NOT basketball's `/games`) — a different
  shape, so this needs its own client/backfill, not `backfill.py` reuse.
- **`ingestion/soccer_backfill.py`** (NEW): mirrors `backfill.py`'s structure for API-Sports
  football. Functions (all pure-testable where possible):
  - a small football client (header auth, paging, pacing) OR reuse `APISportsClient` if its
    auth/host is parameterizable — **check `api_client.py` first**; if it hardcodes the
    basketball host/auth, add a football variant rather than contort it.
  - `backfill_fixtures(client, engine, season)` → upsert `games` (id+offset, sport='mls', date,
    home/away team ids+offset, status) AND, gated on `is_soccer_final(status)` (`{FT,AET,PEN}`),
    write `team_game_stats('points')` = each team's goals (mirror NBA `nba_team_points_rows`).
  - `backfill_player_stats(client, engine, finished_fixtures)` → `/fixtures/players`, extract
    `shots.total`/`shots.on`/`tackles.total` into `player_game_stats` with stat_types matching
    `STAT_MAPS['mls']`'s values (so props settle).
  - teams/players upsert as needed (from fixture + player-stats payloads).
  - CLI `--season` (loop 2022/2023/2024 for the historical backfill), `--only fixtures|stats|all`.
- **`STAT_EXTRACTORS['mls']`** (if that registry exists in backfill.py — confirm) OR the soccer
  extractor lives in `soccer_backfill.py`. Player stat_type names must equal `STAT_MAPS['mls']`
  values (see #2) so odds line up with actuals.
- **Backfill 2022–2024 live** (architect lane): counts, spot-check scores vs a known result, FK
  orphans, idempotent re-run, AET/PEN games load.
- Tests: pure `is_soccer_final`, `soccer_team_points_rows`, a player-stat extractor on a fixture
  payload fixture (no live API). Fake-engine for any DB-touching unit.

**#1 acceptance:** MLS 2022–2024 in `games`/`players`/`player_game_stats`/`team_game_stats`;
`team_game_stats('points')` present for finals incl. AET/PEN; stat_types match #2's map;
idempotent; no FK orphans; MLB/NBA/NFL untouched.

### MLS #2 — Builder product (odds + wiring + settlement + chain + dashboard)
Depends on #1's data. NBA-style layer. Its own spec/plan after #1 lands, but scoped here:

- **`ingestion/odds_ingest.py`:** `STAT_MAPS['mls']` = `{shots:shots, shots_onGoal:shots_on_goal,
  tackles:tackles}` (values = the `player_game_stats.stat_type` names #1 writes);
  `GAME_MARKETS['mls']` = `{match_total: ("points","all","game")}` (betTypeID `ou`). Add `mls`
  to the `--sport` choices; SGO fetch uses `odds_league_id="MLS"`.
- **`optimizer/builder.py` / `builder_core.py`:** `TEAM_MARKETS['mls']=("match_total",)`,
  `MARKET_GEOMETRY["match_total"]="ou"`, `SLATE_WINDOW_DAYS['mls']=0` (daily), `_team_class`
  → `game_tier` for mls. If the game-market name `match_total` is new (vs NFL/NBA's
  `full_game_total`), it needs its own `MARKET_GEOMETRY` + settlement dispatch entry — **prefer
  reusing `full_game_total`** if the semantics match (total O/U vs a line) to inherit the
  existing total-scoring path with zero settlement change. Decide in #2's plan.
- **Settlement:** player path reads soccer `player_game_stats` like any sport (zero change);
  match-total scores off `team_game_stats('points')` via the existing total path (`game_total`
  + over/under `settle_leg`). Confirm `leg_status` final-status set includes soccer's `AET`/`PEN`
  (extend `_FINAL_GAME_STATUSES` to `{FT, AOT, AET, PEN}` — MLB/NFL/NBA never emit AET/PEN).
- **Chain (`scripts/daily_chain.sh`):** `_mls_daily_build` (odds + player ×2 + game ×2,
  `--max-leg-reuse 2 --save`), daily best-effort after the MLB pre-game save; `mls_scores`
  refresh via `soccer_backfill --season current --only fixtures` before `settle`. (Live only with
  the paid plan; the block is inert/empty on free until then.)
- **Dashboard:** MLS tab in `SportTabs`; `SPORT_CFG['mls']` (daily "Today's" heading, tier2=game
  = match totals, honest empty state); MLS team nicknames in `teamNames.ts`.
- **Record:** `/parlay-builder/record?sport=mls` — already sport-generic (COALESCE), no change.

**#2 acceptance:** MLS parlays build against 2022–2024 slates (structural — a player shots leg +
a match-total leg construct + settle hit/miss/void against real historical results); MLB/NBA/NFL
byte-unchanged; Budgerr surfaces unchanged; dashboard MLS tab honest empty state; full suite green.

---

## Non-goals
- No soccer model / features / edges (§16 — builder never uses `model_prob`).
- No `ml3way`/spread/2-way-ml markets in v1 (deferred).
- No live-odds verification now (paid-gated; structural build on 2022–2024).
- No schema migration (separate `sport` key + existing `games`/`team_game_stats`/`game_lines`).
- Champions League is a **follow-on** (its own small track: `sport='ucl'` + league id + a tab,
  reusing the shared soccer maps + `soccer_backfill.py`).

## Open items for the #1 plan
- Confirm whether `ingestion/api_client.py`'s `APISportsClient` can be parameterized for the
  football host + `x-apisports-key` header, or whether a small dedicated football client is
  cleaner (graphify `api_client.py` first).
- Confirm the `STAT_EXTRACTORS` registry location (backfill.py) and how NFL/NBA register, to
  mirror for the soccer extractor.
