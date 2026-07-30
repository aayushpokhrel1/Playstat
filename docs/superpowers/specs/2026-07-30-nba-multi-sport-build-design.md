# NBA Multi-Sport Builder — Design (2026-07-30)

**Status:** DESIGNED, user-confirmed 2026-07-30 (both tiers; `game_tier` class; historical
score backfill now). Ready for `writing-plans`.

**Strategic frame:** README §16 roadmap item 3 — NBA is the cheapest new-sport add. The
builder core, settlement, chain, dashboard, and record are already sport-parameterized by
the NFL track (§14.3, sub-projects #1–#4). This build fills in NBA's market maps and closes
two ingestion gaps found 2026-07-30. **No NBA prediction model** — the builder ranks on
de-vigged market probability (§15.8 guardrail 1); the shelved NBA model (§16) stays
irrelevant.

**Scope:** both tiers — player props (points/rebounds/assists O/U) AND game markets
(full-game spread / moneyline / total). Structural verification happens now; **live odds are
offseason-gated to ~October** (NBA season tips ~mid-October), so the `--dry-run` statID/betType
pin and the first live card wait for preseason, exactly like NFL TASK 2.

---

## Current state (verified 2026-07-30, live DB + source)

- **Data loaded:** 1,376 NBA games (1,310 `FT` + 66 `AOT`), one season 2023-10-05 → 2024-06-18.
  `player_game_stats` has `points`/`rebounds`/`assists` (15,154 rows each) — **these match
  `STAT_MAPS['nba']`'s settle values exactly**, so player props are settleable today.
- **`STAT_MAPS['nba']`** = `{points, rebounds, assists}` — already exists
  (`ingestion/odds_ingest.py:19`); `--sport nba` is the CLI default. **No change.**
- **`team_game_stats` is EMPTY for NBA** — `backfill.py:backfill_games` stores only
  game_id/date/teams/status, never final scores. Game-market settlement reads
  `team_game_stats('points')`, so this is the load-bearing gap (identical to the NFL #3 gap).
- **No `GAME_MARKETS['nba']` / `TEAM_MARKETS['nba']`** — game markets are unwired.
- Builder is sport-parameterized: `--sport`, `SLATE_WINDOW_DAYS`, `g.sport` filters, `?sport`
  COALESCE, `TEAM_MARKETS[sport]`, `MARKET_GEOMETRY`, `_team_class`. Settlement's player-leg
  path AND the NFL #3 game-market scoring (total/spread/moneyline vs final scores, margin/
  winner/push→void) are **sport-generic** (dispatch on market name + `kind`/`class`, never on
  sport). Dashboard has `SportTabs` + per-sport `SPORT_CFG`. Record is `?sport`-filtered.

---

## The two gaps to close (`ingestion/backfill.py`)

### G1 — write NBA final scores into `team_game_stats('points')`
Mirror NFL's pure `team_points_rows` (`ingestion/nfl_backfill.py:202`). The scores ride along
in the `/games` response `backfill_games` already fetches — `scores.home.total` /
`scores.away.total` (API-Sports basketball schema; **the exact field path is probe-confirmed**
against a live/historical `/games` response before the backfill runs, same discipline as NFL's
SGO statID pinning — do NOT guess it into production). Add a pure helper
`nba_team_points_rows(game, game_id, home_team_id, away_team_id)` returning the two
`{team_id, game_id, stat_type:'points', value:int}` rows (empty when a score is missing/None),
and upsert them inside `backfill_games`'s loop keyed on `["team_id","game_id","stat_type"]`.
Settlement then reads NBA scores exactly like MLB `runs_inning_1` / NFL points — **zero
settlement changes**.

### G2 — treat `AOT` (after-overtime) as a final/played status
`AOT` is a real final status (66 loaded games). `backfill_games:99`'s finished filter is
`status == "FT"` only — so **every overtime game currently skips box-score loading** (latent
bug) and would skip score-writing + settle. Introduce a single predicate
`NBA_FINAL_STATUSES = {"FT", "AOT"}` (or `is_final(status)`) and use it for: the `finished`
filter (box-score load), G1's score-writing gate, and anywhere the ingest keys off `FT`.
Player/game-market **settlement** already keys on stat-row presence + `game_lines`/final
scores, not the `games.status` string, so it needs no status change — but confirm the
candidate-leg loaders' "unfinished game" predicate (`status != 'FT'`) does not wrongly admit
an `AOT` game as still-live; if it filters on `!= 'FT'` it must exclude `AOT` too (i.e. use
"not in final statuses"). **This is the one settlement-adjacent check to verify in review.**

---

## Market maps

### `ingestion/odds_ingest.py`
Add `GAME_MARKETS['nba']` — full-game spread / moneyline / total, same shape as NFL:
```python
"nba": {
    "full_game_total":     ("points", "all", "game"),  # betTypeID ou
    "full_game_spread":    ("points", "all", "game"),  # betTypeID sp
    "full_game_moneyline": ("points", "all", "game"),  # betTypeID ml
},
```
`bettype_for_market` + `_MARKET_BETTYPE` are unchanged (sp/ml/ou already generic).
`SPREAD_LINE_FIELD` (currently `"bookSpread"`) and the sp/ml betTypeIDs are **preseason
`--dry-run`-pinned** — SGO shares betTypeIDs across sports, so NFL's values are the strong
prior, but probe-confirm against the live NBA feed before first ingest (§14.6 discipline).

### `optimizer/builder.py` + `optimizer/builder_core.py`
- `TEAM_MARKETS['nba'] = ("full_game_total", "full_game_spread", "full_game_moneyline")`.
- `MARKET_GEOMETRY`: the three NBA markets **reuse the identical market names** as NFL, so the
  existing `full_game_total: "ou"`, `full_game_spread/moneyline: "homeaway"` entries already
  cover NBA — **no new geometry keys needed** (confirm the names match; if reused verbatim,
  `MARKET_GEOMETRY` needs no edit).
- `SLATE_WINDOW_DAYS['nba'] = 0` — **daily cadence like MLB** (NBA plays a single-day slate),
  NOT NFL's weekly `4`.
- `_team_class`: NBA team markets save as **`class='game_tier'`** (shared with NFL — full-game
  spread/ML/total are semantically NFL-shaped, not MLB's fixed-line NRFI/F5). Change
  `_team_class` to `"game_tier" if sport in ("nfl", "nba") else "team_tier"`.

Because NBA reuses the same market names + `game_tier` class, the API tier plumbing
(`tier=game`→`game_tier`), the record label, and the settlement scoring **all match
automatically** — no api/main.py or settle.py changes for the game tier.

---

## Chain wiring (`scripts/daily_chain.sh`)

NBA builds + settles **daily** (like MLB, no gate — unlike NFL's Thursday gate). All NBA steps
are **best-effort / non-fatal** (logged, wrapped so a failure never aborts the MLB chain or
pages), mirroring the NFL wrapper pattern:
- Build (daily): `odds_ingest --sport nba` → builder player ×2 (`--target-payout 1.4` / `2.0`)
  → builder game-tier ×2 (`--team-only --target-payout 1.4` / `2.0`), all
  `--tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save`.
- Settle (daily): `backfill --sport nba --only games` (refresh scores/status) **before** the
  shared `settle` step, so each game settles the night it finishes.
- `window_days` defaults to `SLATE_WINDOW_DAYS['nba']=0` (no `--window-days` override needed).

Confirm the NBA build block placement does not delay the pre-game MLB builder save (§15.9 item
7A — the MLB card must land pre-game; NBA steps go after MLB's builder saves or are ordered so
MLB is never blocked).

## Record (`api/main.py`)
`/parlay-builder/record?sport=nba` + `/record/daily?sport=nba` — already sport-generic via
`COALESCE(legs->>'sport','mlb')=:sport`. **No change.** NBA's records won't pool with MLB/NFL.

## Dashboard (`web/app/builder/`)
- `SportTabs`: add an **NBA tab** (`/builder?sport=nba`), server-rendered via the existing
  async `searchParams` pattern.
- `SPORT_CFG['nba']`: daily "Tonight's" heading (like MLB, NOT NFL's "This week's"); tier-2
  fetch = `tier=game` (game-tier total/spread/moneyline copy); honest offseason empty state,
  e.g. "No NBA parlays yet — the nightly card builds once the season opens ~October."
- MLB + NFL rendering must stay **byte-identical** (add a branch, don't alter existing configs).
- `web/app/lib/teamNames.ts`: add the 30 NBA team nicknames to the static nickname map (naive
  last-token splitting fails on e.g. "Trail Blazers"; a lookup table is the only correct
  approach — same rationale as the MLB/NFL maps). Needed for `LegMatchup` rendering.

---

## Historical final-score backfill (architect reserved lane)
After G1/G2 land + probe-confirm the score field path: re-run the NBA games ingest once
(`backfill --sport nba --only games` per season — a handful of API-Sports calls, scores already
in the response, no per-game fetch) to populate `team_game_stats('points')` for the 1,376
loaded games. This enables **verifying game-tier settlement against real final scores now**,
not only structurally. Idempotent re-run (upsert on `team_id/game_id/stat_type`); spot-check a
few games' scores against a known result; check for FK orphans; verify AOT games now load.

---

## Testing + guardrails
Pure / fake-engine tests only — **no live DB** (`ingestion.db.get_engine()` is LIVE; follow the
fake-engine isolation pattern in `tests/test_builder_record_api.py` /
`tests/test_parlay_builder_api.py`):
- `GAME_MARKETS['nba']` / `TEAM_MARKETS['nba']` fixture assertions (present, correct shape).
- `nba_team_points_rows`: two rows for a scored game; empty for missing/None score; int coercion.
- `is_final` / AOT: `FT`→final, `AOT`→final, `NS`/`S`→not-final.
- `MARKET_GEOMETRY` / `_team_class`: NBA game markets resolve ou/homeaway + `game_tier`.

§15.8 guardrails BINDING: rank only on devig market prob; `market_prob ≥ 0.55`; 2–4 legs;
across-game only; paper-only; NO "+EV"/edge/value/green language. **Expected honest behavior:**
the NBA game tier is mostly heavy moneyline favorites (no NRFI-like near-coin-flip market that
still clears 0.55), so it will often be thin or empty — that is correct, not a bug.

## Non-goals
- No NBA model / features / edges (§16 — model shelved; builder never uses `model_prob`).
- No live odds ingest verification now (offseason; preseason `--dry-run` pins the IDs — TASK 2-style).
- No schema migration — `game_lines` already carries `home_odds`/`away_odds` (NFL #3 migration
  007); `team_game_stats` already exists. NBA reuses all of it.

## Acceptance criteria
1. `GAME_MARKETS['nba']` + `TEAM_MARKETS['nba']` + `SLATE_WINDOW_DAYS['nba']=0` + NBA
   `_team_class='game_tier'` present; MLB/NFL byte-unchanged.
2. `backfill.py` writes `team_game_stats('points')` for NBA (G1) and treats `AOT` as final (G2);
   historical backfill populates all 1,376 games; AOT games now load box scores.
3. Structural verification: `builder --sport nba` player + game-tier loaders run (0 legs
   offseason until odds land, but no crash); MLB/NFL builder byte-behavior unchanged.
4. Game-tier settlement verified against ≥1 real NBA final score (post-backfill) — a total or
   spread leg settles hit/miss correctly, a push voids.
5. Chain: NBA steps daily, best-effort, never abort MLB; MLB pre-game save not delayed.
6. Dashboard NBA tab renders honest offseason empty state; MLB/NFL byte-identical; `tsc`/
   `next build` clean.
7. Record `?sport=nba` returns [] (until settled) / correct grouped data; Budgerr surfaces
   (`tier=all`/`team`/`player`, no-`sport`) unchanged.
8. Full pytest suite green (352 + new NBA tests); README §11/§13/§14/§16 updated same commit.
