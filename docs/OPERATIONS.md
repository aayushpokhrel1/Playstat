# Operations

Running, scheduling, verifying and deploying Playstat, plus the environment traps
that have actually cost time and the consumer contract that constrains what can
change.

Basic local setup is in the [README](../README.md#running-it). This file covers the
live setup.

## The scheduled jobs

Three launchd jobs, configured outside this repo at `~/Library/LaunchAgents/`. The
chain body lives in version control at [`scripts/daily_chain.sh`](../scripts/daily_chain.sh),
which the plist invokes via `ProgramArguments` - it is deliberately **not** a
`bash -c` string in the plist, so it can be reviewed and diffed.

| Job | Time (ET) | Does |
| --- | --- | --- |
| `com.playstat.mlb` | 08:30 | Ingest, build the day's cards, settle |
| `com.playstat.mlb.late` | 17:30 | Confirmed-lineup card + 2nd price snapshot |
| `com.playstat.mlb.close` | 19:45 | 3rd price snapshot (closing proxy) |

**The two later jobs suppress themselves if they fire late.** A stale card or a
post-game "closing" line is worse than nothing, so late firing is a no-op rather
than a bad write.

**The wake-arm arrangement must be a FAN, not a CHAIN.** An earlier version armed
the afternoon wake from the morning job: 08:25 wake → 08:35 arm → 17:25 wake. Every
link depended on the previous one, so a Mac that was off at 08:25 armed nothing and
silently lost the whole afternoon. Each wake is now armed independently.

**Coverage varies more than a blended average suggests.** The share of games still
unstarted at the 17:30 trigger is a weekday/Sunday blend - a single headline
coverage figure badly understates the variance. Judge the confirmed-lineup job by
day type, not by its average.

## Verifying

```bash
python -m pytest
```

~305 cases. `optimizer/builder_core.py`'s tests are DB-free and run under `env -i`;
CI runs the whole suite on Python 3.11.

**Tests are necessary and have repeatedly not been sufficient here.** Every one of
the following passed its unit tests and then broke in production, because the tests
used fixtures where reality had a different shape:

- `--save` crashed on invalid JSON. `model_prob` arrives as `NaN` from a `LEFT
  JOIN`; pandas keeps `NaN` in a float column rather than `None`, and `json.dumps`
  emits a bare `NaN`, which Postgres rejects.
- Settlement crashed the moment real rows existed. psycopg2 returns JSONB
  **already parsed**, so the `{class, legs}` wrapper arrives as a `dict` and
  `json.loads(dict)` raises `TypeError`. It had no-op'd harmlessly until real data
  arrived. The same root cause then 500'd `GET /parlay-recommendations`, because
  the first fix only touched `modeling/settle.py`.
- A `float(None)` 500'd the line-movement endpoint the first time a real moneyline
  leg appeared (`line_value` is nullable; every fixture had a float).
- Three code paths still referenced just-dropped tables after the model deletion,
  because verification checked the endpoints that read saved rows but not
  `load_legs`, and the tests use fake engines.

**So: after a change to a write path or a DB-shaped boundary, run it against the
real database before calling it done.** The fake-engine tests cannot see these.

## Deploying and secrets

The API runs as an always-on launchd service (`com.playstat.api`), not a
manually-started dev server, so it survives logout and restarts on crash. Logs go
to `api.log` / `api.error.log` at the repo root (gitignored).

**The service does not run with `--reload`.** Changes to `api/` need a manual
restart or they look like they did not work:

```bash
launchctl kickstart -k gui/$(id -u)/com.playstat.api
```

### Auth

Both halves are **off by default** - with the env vars unset, everything behaves
as it did before auth existed, and one env flip reverts.

- **API** (`.env` / launchd env): `AUTH_ENABLED=true` and `PLAYSTAT_API_KEYS=dashboard:<key>,budgerr:<key>`
  (comma-separated `name:key` pairs; names are per-consumer labels so one can be
  revoked without the others). Every endpoint then requires a matching
  `X-API-Key` header and returns 401 otherwise.
- **Dashboard** (`web/.env.local`, gitignored - see `web/.env.local.example`):
  `PLAYSTAT_API_KEY` (attached server-side by `web/app/lib/api.ts`; it never
  reaches the browser), `DASHBOARD_USER`, `DASHBOARD_PASSWORD_HASH`, and
  `SESSION_SECRET` (unset = login disabled). Sessions are HMAC-signed httpOnly
  cookies (`playstat_session`, 7 days), verified by `web/proxy.ts` (Next 16's
  renamed middleware).

Generate the credentials:

```bash
node web/scripts/hash-password.mjs <password>
```

```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

### Deployment target

`Dockerfile` at the repo root builds on `python:3.11-slim`, matching the venv and
CI. **`libgomp1` is required** - xgboost links OpenMP at runtime and the slim image
does not ship it.

The researched decision, unchanged: **Tailscale Serve** is the right first step -
free, automatic HTTPS inside the tailnet, zero public attack surface, and zero
migration (it points at the existing localhost ports). The cost is the phone
needing the Tailscale app. Funnel adds a public URL and public surface; a VPS
($5-10/mo) is the only option that serves while the laptop is off, and costs a
Postgres/systemd/caddy migration.

`.env` and `web/.env.local` are fine on a personal laptop. A deployment needs real
secret management and HTTPS before an API key crosses a network.

## Quota

**SportsGameOdds** free tier: **2,500 entities/month, 10 req/min** (which is why
`odds_client.py` paces at 6.5s). Check usage any time with `GET /v2/account/usage/`
,  undocumented, but it works with our key.

**The billing unit is entities, and 1 returned event = 1 entity** - measured
against `/account/usage`, not estimated. Two consequences:

- **Richer parsing of a response we already receive is free.** This is why line
  shopping across six books cost nothing: the `byBookmaker` breakdown was already
  in every response and was being discarded.
- **Narrowing a pull to the current slate is what makes three daily pulls
  affordable.** All three snapshots together cost less than the single unnarrowed
  pull they replaced. The quota was exhausted twice before narrowing.

Hourly game-day pulls would be ~5,400/month and blow the free tier in under a
week. That is why alerting is deferred, not because it is hard.

**API-Sports** free tier caps at seasons 2022-24, so any sport depending on it is
gated on a paid plan for current data (API-Football Pro ~$19/mo covers all seasons
and all competitions including EPL). `statsapi.mlb.com` and `api-web.nhle.com` are
free, key-less and current - which is why MLB and NHL are the live-for-free sports.

## Monitoring

healthchecks.io free tier: 20 checks, schedule-aware missed-ping alerts, a bare
`curl` suffices. Email and webhook alerts are free; SMS is not, as of mid-2026.
ntfy.sh free: 250 msgs/day, no account. Self-hosted uptime-kuma was rejected - no
cron-schedule awareness, and it adds a service to babysit.

The chain pushes on failure. **A failure push is not automatically a real
failure**: the retired `optimizer.parlay` step OOM-died nightly and pushed a false
alert every morning until it was replaced, and a hand-written sentinel was needed
to stop the catch-up re-run storm. If an alert recurs on a schedule, suspect the
step before the infrastructure.

## Environment traps

- **`tzdata` is required on Windows and is easy to miss.** The code calls
  `ZoneInfo("America/New_York")`; macOS and Linux resolve that against the system
  tz database, Windows has none, so **every test module that imports a
  timezone-aware path fails at collection** - 29 collection errors that look
  nothing like a missing timezone. It is now in `requirements.txt`.
- **The project lives under `~/dev/playstat`, not `~/Documents`.** `~/Documents` is
  iCloud-synced on this machine, which caused intermittent file-read deadlocks for
  launchd-spawned processes specifically.
- **Chain runtime was the binding constraint on landing a card pre-game.** The
  chain used to recompute over all history every night - re-deriving ~2.3M
  historical rolling-feature rows and retraining a model per stat. Removing the
  model steps cut ~1.5-2h (`backtest` alone was ~64 min).
- **Ingestion writers that upsert row-at-a-time are slow enough to matter.** A
  full NFL backfill took >10 min; row-at-a-time feature writes took ~35 min/season
  against a couple of minutes batched. `nfl_backfill.py` and `mlb_backfill.py` are
  still row-at-a-time.
- **XGBoost was never seeded**, so historical calibration numbers in old specs
  will not reproduce exactly. Expected library stochasticity, not a bug. (Moot for
  live behaviour - the models are deleted.)

## Consumer contract (Budgerr)

[Budgerr](https://github.com/aayushpokhrel1/Budgerr) reads this API. It is a
one-way, read-only HTTP dependency: no shared database, no write access back.
`CORSMiddleware` is configured (`CORS_ORIGINS`) so Budgerr's browser frontend can
call directly, and `allow_headers=["*"]` covers the API-key header on preflight.

**Live contract surfaces - treat any change as breaking:**

| Endpoint | Notes |
| --- | --- |
| `GET /parlay-builder/saved` | The forward source. `?tier=all&limit=100` fetched once, partitioned client-side by leg kind. `tier` semantics **and** response shape are both contract. |
| `GET /games` | Resolve a team leg's matchup via `game_id` - team legs are game-level markets with no team in `label`. |
| `GET /box-scores` | - |

**Additive-only.** New fields are fine; renames, removals and reordering are not.
`/parlay-builder/saved` returns newest-N-regardless-of-date, and Budgerr's
partitioning relies on that ordering - which is why the dashboard's "tonight's
slate" scoping is done **client-side** in `web/app/builder/` rather than by
changing the endpoint's default.

**Removed 2026-08-06, now 404:** `GET /edges`, `GET /game-predictions`,
`GET /parlay-recommendations`. Budgerr migrated onto `/parlay-builder/saved` and
acked before removal; the wind-down was phased (RFC 8594/9745
`Deprecation`/`Sunset`/`Link` headers with empty bodies, then route removal).

**`GET /parlay-builder/line-movement` is dashboard-only by CONVENTION, not by a
gate.** It carries no auth distinct from the rest of the app, so any valid
`PLAYSTAT_API_KEYS` holder can already call it. Left as-is deliberately - adding a
gate now would be a behaviour change on a live surface - but documented rather
than implied, and Budgerr was told directly rather than left to discover it.

**Verification standard for a change near these surfaces: byte-comparison, not
assertion.** The line-movement addition was verified by diffing
`/parlay-builder/saved?tier=all&limit=100` between the live port (pre-change) and a
spare port (post-change): byte-identical at 103,637 bytes, `/games` likewise.

**The team-market enum is MLB-specific.** Budgerr's `{first_inning_runs, f5_runs}`
is correct and complete for MLB, verified against `TEAM_MARKETS`. Every other sport
uses `full_game_*`, so pointing a consumer at a non-MLB builder needs the enum
extended first. The warning lives at the code in `optimizer/builder.py`.

### CLV coordination - plumbing agreed, conclusions dark

Budgerr shipped its half (2026-08-13): `bet_legs` carry nullable `game_id`,
`player_id` and `market`, populated at log time, plus the already-stored
`line_value`. No `closing_odds` field, no value/edge naming, nothing user-facing.

What is settled and stable to build against: the join keys and the
**moved-`line_value`-is-EXCLUDED-not-compared** rule (both in
[ARCHITECTURE.md](ARCHITECTURE.md#line-movement)), the attach surface
(`/parlay-builder/saved`), and the 08:30/17:30/19:45 cadence - which makes a
scheduled consumer-side backfill the right pattern over a live read.

What is **not** settled: the read surface. The agreed direction is a lookup keyed
to `(game_id, player_id/market, line_value)` rather than backfilling the saved-legs
JSONB, but nothing ships until the CLV gate resolves (PRODUCT.md roadmap item 1).

Remaining work when it does: **(i)** our lookup endpoint, **(ii)** their scheduled
backfill. Parked on both sides until we ping. Anything we ship is named for
*movement*, not value - guardrail 2.
