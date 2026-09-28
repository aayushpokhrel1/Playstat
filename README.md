# Playstat - a market-ranked low-risk parlay builder (paper trading)

> **Paper trading only. Nothing in this repo places a real bet, and no part of it
> claims an edge over the market.**

Playstat builds **low-risk parlays out of sportsbook prices**, tracks them in a
**paper ledger**, and measures - honestly - whether they would have made money.

The core idea in one line: **rank legs by the de-vigged market probability, not by
a model.** The book's own price, with its margin mathematically removed, is the
best available estimate of what will happen. The builder's job is to find the
combination of favourites that reaches a target payout with the highest chance of
hitting: the *least-bad* combination, not a winner.

**What it does each day (MLB):**

- Ingests games, box scores and sportsbook lines (player props + game markets).
- Shops each leg across six books and keeps the best price.
- Builds cards at 1.4x and 2.0x payout targets, plus a team-market tier and a
  same-game NRFI+F5 tier.
- Builds a second, higher-confidence card at 17:30 ET once **lineups are posted**,
  which cuts legs that would void because the player never appeared.
- Settles everything against real box scores into a paper ledger.
- Takes three price snapshots a day so **closing-line movement** is measurable.

**There is no prediction model.** One was built, measured, and **deleted**
(code and tables, 2026-08-06) because it could not resolve individual games.
`model_prob` still appears in some payloads and is always `null`; it is context
only and never affects ranking. See [PRODUCT.md](PRODUCT.md) for why, and
[docs/FINDINGS.md](docs/FINDINGS.md) for the measurements that settled it.

**The honest position on profitability:** the builder is structurally
negative-EV, by arithmetic rather than by bad luck, and the repo says so
everywhere it reports a number. [PRODUCT.md](PRODUCT.md) states the position and
the guardrails that keep it stated; [docs/FINDINGS.md](docs/FINDINGS.md) has the
evidence.

## Sports

**MLB** is live and produces cards daily. **NFL, NBA, MLS, UCL and NHL** are
built and structurally verified but gated - NBA/MLS/UCL need a paid stats plan,
NFL is seasonal, NHL flips on for free at puck-drop (~Oct). Details and the
per-sport gate in [PRODUCT.md](PRODUCT.md#multi-sport-status).

## Stack

| Layer | Choice |
| --- | --- |
| Ingestion | Python; `statsapi.mlb.com`, SportsGameOdds, The Odds API, `api-web.nhle.com`, API-Sports, nflverse CSVs |
| Database | PostgreSQL (`db/migrations/`, applied in order) |
| Builder / optimizer | Pure-Python search in `optimizer/` (`builder_core.py` is DB-free) |
| API | FastAPI (`api/`) |
| Frontend | Next.js 16 + TypeScript (`web/`) |
| Scheduling | launchd (three daily jobs; see [docs/OPERATIONS.md](docs/OPERATIONS.md)) |
| Tests / CI | pytest (~305 cases) + GitHub Actions |

Module map and how the pieces fit: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Running it

Requires Python 3.11 and a PostgreSQL database.

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `DATABASE_URL` plus the API keys you need.
Apply migrations in `db/migrations/` in filename order.

Ingest and build a slate (MLB):

```bash
python -m ingestion.mlb_backfill --only games && python -m ingestion.odds_ingest --sport mlb
```

```bash
python -m optimizer.builder --target-payout 1.4 --tolerance 0.10 --top-n 5
```

Add `--save` to persist the cards, `--team-only` for the team-market tier, and
`--sport <nfl|nba|mls|ucl|nhl>` for another sport. Settle finished games:

```bash
python -m modeling.settle
```

Serve the API and the dashboard:

```bash
uvicorn api.main:app --reload
```

```bash
cd web && npm install && npm run dev
```

Run the tests:

```bash
python -m pytest
```

The API also runs as an always-on launchd service in the live setup, auth is off
by default, and the daily chain is a script rather than a cron string. All of
that, plus the environment traps that have actually bitten,
is in [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Where things are written down

| File | What it holds |
| --- | --- |
| `README.md` | This file: what it is, the stack, how to run it |
| [PRODUCT.md](PRODUCT.md) | Product truth: who it is for, guardrails, decisions, roadmap, what is deferred and why |
| [DESIGN.md](DESIGN.md) | The visual system: tokens, type, colour, component patterns |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the system is put together, and the data model |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Run, verify, schedule, deploy, secrets, quota, consumer contracts, environment traps |
| [docs/FINDINGS.md](docs/FINDINGS.md) | What has been measured, and what it rules out |
| `docs/superpowers/` | Per-feature specs and plans, written before building |
| `CLAUDE.md` | Conventions for working in this repo |
