# Architecture

How the system is put together today. For *why* it is shaped this way, see
[PRODUCT.md](../PRODUCT.md); for running and operating it, see
[OPERATIONS.md](OPERATIONS.md).

> **There is no prediction model.** The model code and its six tables were deleted
> 2026-08-06. If you find a reference to `model_predictions`, `edges`,
> `backtest_runs`, `clv_records`, `game_predictions` or `game_edges`, it is a bug
> or a stale doc, not a table.

## Shape

```
statsapi.mlb.com ──┐                              ┌─> paper ledger (settlement)
API-Sports        ─┤                              │
api-web.nhle.com  ─┼─> ingestion ─> PostgreSQL ─> builder ─> saved cards ─> dashboard
SportsGameOdds    ─┤   (games,       (de-vig,     (exact      + Kelly       + record
The Odds API      ─┘    players,      line         two-axis     stakes        + line
                        box scores,   shopping)    search)                    movement
                        odds)
```

## Modules

| Path | Holds |
| --- | --- |
| `ingestion/` | One backfill module per data source, plus `odds_ingest.py` (all sports, `--sport`), `mlb_lineups.py`, `theodds_client.py` (sharp reference), `config.py` (per-sport config and ID offsets), `db.py` |
| `optimizer/builder_core.py` | **Pure math, no DB.** Leg normalization, the floor, same-game detection, and the search. Runs under `env -i`; this is where the tests live. |
| `optimizer/builder.py` | DB loading, persistence, CLI. `TEAM_MARKETS`, `SLATE_WINDOW_DAYS`, the confirmed-lineup filter, `save_builds`. |
| `optimizer/devig.py` | `devig` / `odds_to_probability`. Extracted from the deleted model; the builder's ranking depends on it. |
| `optimizer/stake.py` | ¼-Kelly sizing per parlay-as-one-bet, with a same-night exposure cap. Stakes **0** when `p·d ≤ 1`. |
| `optimizer/line_movement.py` | Snapshot comparison. Owns the comparison rules (see below). |
| `optimizer/parlay.py`, `optimizer/team_parlay.py` | Out of the daily chain. Kept as helpers and as the tested substrate for same-game work. |
| `modeling/settle.py` | The paper ledger. `settle_builder_parlays()` is the only live path. |
| `modeling/correlation.py` | NRFI×F5 empirical joint frequencies for the same-game tier. |
| `api/main.py` | FastAPI. Read-only, additive-only. |
| `web/` | Next.js 16 dashboard. Read `web/AGENTS.md` for the Next 16 caveats. |
| `db/migrations/` | Numbered SQL, applied in filename order. |
| `scripts/daily_chain.sh` | The scheduled chain body, version-controlled rather than living in a plist string. |

## The builder

Ranking is market-only, and that is the load-bearing property. Three things
follow from it:

**Leg loading** takes the latest `prop_lines` and `game_lines` per market
(`DISTINCT ON … ORDER BY pulled_at DESC`), de-vigs both sides, keeps the
**favourite**, and drops anything under the 0.55 floor. One-sided lines (~8% of
live MLB lines) cannot be de-vigged and are skipped. Candidates are restricted to
the current slate - `SLATE_WINDOW_DAYS` is 0 for MLB and 4 for NFL's Thu-Mon card
,  which is what stops a futures line being mixed into tonight's parlay.

**The search is exact, not a heuristic.** A game-structured DFS picks a set of
games and then one leg from each, so same-game pairs are never generated at all.
Per-game price dedupe drops equal-price legs (same price means the same payout, so
keep the most probable). An exact heap-aware prune on both axes - which are exact
duals, ranking by one quantity subject to a floor on the other - makes it provably
identical to a global brute force. That equivalence is not asserted but verified
against a brute-force oracle in `tests/test_builder_search_exactness.py`,
including adversarial favourite-heavy slates. Both axes finish exhaustively on
real slates at the production `top_n`.

This replaced an earlier progressive-widening early-stop that was only exact under
uniform-vig book lines, and before that a flat enumeration that OOM-died nightly
on `C(1060,3)` ≈ 198M combinations.

**Persistence** writes the top-N into `parlay_recommendations` with a JSONB
wrapper `{class, legs: [...]}`. There is **no `ev` field** - that is guardrail 2,
not an omission.

### Card classes

One `kind='builder'` row per card, distinguished by `class`:

| Class | Built by |
| --- | --- |
| `across_game` | The morning chain, 1.4x and 2.0x targets |
| `team_tier` | MLB NRFI/F5 markets |
| `game_tier` | Other sports' `full_game_*` markets |
| `same_game` | The deliberately labelled NRFI+F5 exception to across-game-only |
| `confirmed_lineup` | The 17:30 ET job, after lineups post |

`construction_signature` dedupe is scoped per `(kind, class, sport)` - see
PRODUCT.md's deliberate non-bugs for why the same construction may legitimately
save twice.

## Settlement

`settle_builder_parlays()` dispatches **per leg** on `leg["kind"]`, because a
builder card can mix player and team legs. This is worth knowing before touching
it: the two older paths were *homogeneous* - one required `player_id` on every
leg, the other `market` on every leg - so neither could score a mixed card, and an
early design draft that claimed settlement needed no new code was simply wrong.

Results land in `recommendation_outcomes` with `bet_type='parlay'`, sharing the
value with the legacy model-ranked rows because the CHECK constraint only allows
`('parlay','edge')`. Tier separation comes from the class in the legs JSONB, not
from `bet_type`.

A leg settles `void` when the game is final but no stat row exists - the player
never appeared. Voids are a real signal, not noise: see FINDINGS.md.

## Line movement

Three price snapshots a day make movement measurable. The comparison rules are
strict and any consumer must mirror them or its numbers silently diverge from
ours:

- Strictly-later snapshot only.
- **Same line only.** A moved `line_value` is **EXCLUDED, not compared** - this is
  the rule most likely to be got wrong.
- Two-sided markets only (a one-sided quote cannot be de-vigged).

Join keys: player legs on `(game_id, player_id, stat_type, line_value)` + side ∈
{over, under}; team legs on `(game_id, market, line_value)` + side ∈ {over, under}
for totals, {home, away} for moneyline.

`line_value` is **nullable** - home/away moneyline markets carry no line. A
`float(None)` on that path 500'd the endpoint the first time a real moneyline leg
appeared, because every test fixture had used a float. Both-None now means "no
line".

## Data model

Core tables, all sport-keyed:

```sql
teams(team_id, sport, name, ...)
players(player_id, sport, name, team_id, ...)
games(game_id, sport, date, home_team_id, away_team_id, status)

player_game_stats(player_id, game_id, stat_type, value)   -- long format
team_game_stats(team_id, game_id, stat_type, value)       -- incl. runs_inning_1
prop_lines(line_id, player_id, game_id, stat_type, line_value,
           over_odds, under_odds, book, pulled_at)
game_lines(game_id, market, line_value, ..., pulled_at)
sharp_lines(...)                                          -- append-only reference
parlay_recommendations(parlay_id, created_at, kind, target_payout,
                       legs jsonb, joint_prob, combined_odds, stake)
recommendation_outcomes(...)                              -- the paper ledger
```

`player_game_stats` and `rolling_player_features` are **long format** keyed by
`stat_type`/`feature` name, not wide NBA-shaped columns. That is what makes a new
sport an ingestion problem rather than a DDL problem.

### The per-sport ID scheme is not clean 100M bands

`games`/`players`/`teams` primary keys are provider numeric IDs with a per-sport
offset, and the offsets in `ingestion/config.py` **do not describe physical
placement**:

- NFL's nominal offset is +200M, but its `game_id` is `200M + season*100000 + …`,
  so it **physically sits at ~402M (season 2023) and climbs +0.1M/season**. Its
  real band is ~400M-410M+.
- UCL was therefore assigned **+500M**, not the next free +100M band - +400M would
  have collided with NFL once raw fixture IDs passed ~2.3M, which is imminent.
  `tests/test_ucl_wiring.py` guards this.
- NHL got **+1B**, not a +100M band: `game_id` is INT4 (max 2,147,483,647) and NHL
  native IDs are already ~2.03e9, leaving no room for a positive offset.
  `nhl_backfill` stores `1e9 + (raw − 2e9)`.

**Any new sport must be range-checked against NFL's real 400M+ span, not slotted
by the next +100M.** The offsets in `config.py` carry this warning at the code.
