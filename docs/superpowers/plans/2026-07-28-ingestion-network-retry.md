# Ingestion network-retry hardening — the chain must survive a transient blip

Spec-complete. The 08:30 daily chain FAILED on **2 of the last 3 mornings**, both
at the very first step (`stats`), aborting the ENTIRE chain — including the
pre-game builder save (§15.9 item 7 A) — and recovering only via a manual restart
hours later (post-game for early slates):

- **2026-07-27**: `requests.exceptions.ConnectionError: RemoteDisconnected('Remote
  end closed connection without response')` mid-fetch, after 1624s.
- **2026-07-28**: `ConnectionError … NameResolutionError … Failed to resolve
  'statsapi.mlb.com'` at boot, after 4s (network not up when launchd fired).

Neither is a code bug in the ingestion logic. Both are **transport-level**
exceptions.

## Root cause (verified in source)

All three ingestion HTTP clients retry on **HTTP status codes only** and let
**transport exceptions escape unretried**. In `MLBStatsClient.get`
(`ingestion/mlb_backfill.py:47`):

```python
for attempt in range(1, MAX_RETRIES + 1):
    self._last_request_at = time.monotonic()
    response = self.session.get(url, params=params, timeout=30)   # <-- raises here
    if response.status_code >= 500 and attempt < MAX_RETRIES:      # never reached on a
        time.sleep(RETRY_BACKOFF_SECONDS * attempt)                #     ConnectionError/Timeout
        continue
    response.raise_for_status()
    return response.json()
```

`requests.exceptions.ConnectionError` (DNS `NameResolutionError`,
`RemoteDisconnected`) and `requests.exceptions.Timeout` are raised by
`session.get(...)` **before** the `status_code` check, so the retry loop's own
`continue` is dead code for exactly the failures we hit. The same gap exists,
identically, in:

- `SportsGameOddsClient.get` (`ingestion/odds_client.py:27`) — the `odds` step.
- `APISportsClient.get` (`ingestion/api_client.py:35`) — the NBA `com.playstat.backfill`.

`MLBStatsClient` is used only by `ingestion/mlb_backfill.py` and covers the
chain's `stats`, `linescores`, `first_inning`, and `f5_runs` calls.

## Approach (LOCKED) — two complementary layers

The two failure modes are distinct and need different guards:

- **Layer 1 (client-level, PRIMARY)** — retry transport exceptions, not just
  status codes. Fixes the mid-run drop (07-27) and short DNS blips.
- **Layer 2 (chain-level safety net, SECONDARY)** — one delayed re-run of a failed
  *network* step. Fixes network-entirely-down-at-boot (07-28), where even a
  handful of sub-minute client retries can't outlast the network coming up.

Rejected: extracting a shared `request_with_retries` helper across the three
clients. Each has bespoke, quota-sensitive status handling (429/403/quota
exhaustion); unifying them risks changing paid-API behavior for a network fix.
Keep the diffs minimal and inline per client.

## Layer 1 — `ingestion/` clients (transport retry)

For each of `MLBStatsClient.get`, `SportsGameOddsClient.get`,
`APISportsClient.get`, wrap ONLY the `self.session.get(...)` call so that a
`requests.exceptions.ConnectionError` or `requests.exceptions.Timeout`:

- on `attempt < MAX_RETRIES`: `time.sleep(RETRY_BACKOFF_SECONDS * attempt)` then
  `continue` (same backoff cadence the status-code path already uses);
- on the final attempt: **re-raise the original exception** (do NOT swallow — a
  genuinely unreachable host must still fail the step loudly).

Catch **only** `(requests.exceptions.ConnectionError, requests.exceptions.Timeout)`
— NOT bare `RequestException`. A 4xx surfaces via `raise_for_status()` *after* a
successful transport and must NOT be retried (it's a real error, e.g. 404). Leave
every existing status-code branch (5xx, 429, 403, quota-exhaustion) exactly as is.

Shape (illustrative, MLB client):

```python
for attempt in range(1, MAX_RETRIES + 1):
    self._last_request_at = time.monotonic()
    try:
        response = self.session.get(url, params=params, timeout=30)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue
        raise
    if response.status_code >= 500 and attempt < MAX_RETRIES:
        time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        continue
    response.raise_for_status()
    return response.json()
```

Keep each client's own `MAX_RETRIES` / `RETRY_BACKOFF_SECONDS` constants and its
existing `raise ... / RuntimeError("Exhausted retries")` tail as-is. Do NOT change
`SECONDS_BETWEEN_REQUESTS` pacing or timeouts.

(Out of scope but note in the report if seen: `ingestion/nfl_backfill.py` — NFL is
not in the nightly MLB chain; leave it unless it trivially shares the same client
pattern, in which case apply the same guard and say so.)

## Layer 2 — `scripts/daily_chain.sh` (delayed re-run of network steps)

The chain runs steps via `_step` (`scripts/daily_chain.sh:103`), `&&`-chained so
any failure aborts. Add a sibling `_step_retry` that runs a step and, on non-zero
rc, sleeps then runs it **once** more before returning its rc; log both attempts.
Apply it to the **network ingestion steps ONLY**: `stats`, `linescores`, `odds`,
`first_inning`. Every compute/model step (`builder_*`, `clv`, `settle`,
`features`, `predict`, `edges`, `backtest`) keeps today's fail-fast behavior — a
64-min `backtest` or a real data bug must NOT be auto-re-run.

- Sleep between attempts: **120s** (gives a boot-time network time to come up).
- Preserve `_step`'s existing per-step timing log lines for both attempts (so the
  `=== step <n>: Ns rc=R ===` history stays intact and greppable).
- Keep the `set -e`/`&&` structure and the `chain OK` / `chain FAILED` + ntfy
  behavior unchanged. `_step_retry` must return the final rc so the `&&` chain and
  the FAILED-push path still work when both attempts fail.

## Tests (`tests/`)

CRITICAL SAFETY: NO test DB; `ingestion.db.get_engine()` is LIVE. These clients do
NOT touch the DB (they're constructed bare), so tests are pure network-mock — no
DB, no real sockets. Inspect `tests/test_odds.py` for the existing client-test
conventions and match them.

Per client (`MLBStatsClient`, `SportsGameOddsClient`, `APISportsClient`),
monkeypatching `client.session.get` and `time.sleep` (assert sleep called, keep
tests instant):

1. `session.get` raises `ConnectionError` on the first `MAX_RETRIES - 1` calls
   then returns a fake `200` → `.get()` returns the parsed body (retried, then
   succeeded). Assert call count == number of attempts.
2. `session.get` raises `Timeout` similarly → same success-after-retry.
3. `session.get` raises `ConnectionError` on **every** attempt → `.get()`
   re-raises `ConnectionError` (or the client's `RuntimeError` tail if that's how
   the loop terminates — assert whichever the code actually does) after exactly
   `MAX_RETRIES` attempts.
4. **Regression:** a fake `404` response is NOT retried and raises `HTTPError`
   (via `raise_for_status`) on the first attempt (call count == 1) — proves 4xx
   still fails fast.
5. **Regression:** existing `5xx` (and, for the odds/api clients, `429`) retry
   behavior is unchanged (a 5xx then a 200 → success after retry).

Use a minimal fake response object (`.status_code`, `.json()`,
`.raise_for_status()` raising `requests.exceptions.HTTPError` for >= 400) — mirror
whatever `tests/test_odds.py` already uses if it has one.

Full suite stays green (currently **251**); you are ADDING tests.

Layer 2 (`daily_chain.sh`) is bash; no unit test. The architect reviews it by
inspection and a local `bash`-level smoke with a stubbed failing command.

## Out of scope (architect does these)
- launchd, git push, live DB writes, the live `:8000` API. Work in the worktree,
  commit there only. The architect reviews diffs, merges, and verifies.
- No API kickstart is expected: these clients are run as CLI subprocesses by the
  chain, not imported by the always-on API — the architect confirms the API does
  not import them and skips the kickstart accordingly.
- The fix takes effect on the next chain run (no service reinstall for a script
  edit); the architect confirms `scripts/daily_chain.sh` still parses and the
  ingestion CLIs still run.
