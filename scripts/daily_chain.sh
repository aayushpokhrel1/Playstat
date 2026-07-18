#!/bin/bash
# Playstat MLB daily chain — the body of the com.playstat.mlb launchd job.
#
# Runs the full morning pipeline, pings healthchecks.io on success, and pushes to
# ntfy.sh on failure. Also self-heals a missed run: launchd fires a missed
# StartCalendarInterval on *wake*, but a *boot* after the trigger time starts the
# schedule fresh and silently skips the day (this happened 2026-07-17: the Mac
# booted 08:47, the 8:30 run never happened, and nothing noticed). So the job is
# also triggered at load and every CATCHUP_POLL_SECONDS, and this script decides
# whether the chain actually needs to run.
#
# Division of labour: this script self-heals, healthchecks.io detects. Anything
# outside the catch-up window is left for healthchecks.io's missed-ping alert
# rather than handled here — that keeps the retry logic dumb and the alerting in
# one place.
#
# Env overrides exist so both the success and failure paths can be smoke-tested
# without touching the live chain (see PLAYSTAT_CHAIN_CMD).

set -uo pipefail

REPO="${PLAYSTAT_REPO:-/Users/aayushpokhrel/dev/playstat}"
PY="$REPO/.venv/bin/python"
CURL=/usr/bin/curl # launchd's PATH is /usr/bin:/bin:/usr/sbin:/sbin — use absolute paths
STATE="${PLAYSTAT_STATE_FILE:-$REPO/logs/.last_success}"

# Catch-up window. Below WINDOW_OPEN the 8:30 calendar trigger has it covered; past
# WINDOW_CLOSE the slate is underway and regenerating recommendations for games
# already in progress is worse than skipping the day.
WINDOW_OPEN="${PLAYSTAT_WINDOW_OPEN:-0830}"
WINDOW_CLOSE="${PLAYSTAT_WINDOW_CLOSE:-1200}"

cd "$REPO" || exit 1

# launchd does not read .env. The pipeline's python modules load it themselves via
# dotenv, so they work regardless — but this wrapper's own curl calls do not, and an
# unset HEALTHCHECK_PING_URL would silently curl an empty URL, so the success ping
# would never land and healthchecks.io would cry wolf every single morning.
if [ -f "$REPO/.env" ]; then
	set -a
	# shellcheck disable=SC1091
	. "$REPO/.env"
	set +a
fi

today=$(date +%F)
now=$((10#$(date +%H%M))) # 10# forces base 10: "0830" is not a valid octal literal

# Already succeeded today — this is what makes the catch-up polling idempotent.
if [ -f "$STATE" ] && [ "$(cat "$STATE" 2>/dev/null)" = "$today" ]; then
	exit 0
fi

if [ "$now" -lt "$((10#$WINDOW_OPEN))" ] || [ "$now" -ge "$((10#$WINDOW_CLOSE))" ]; then
	exit 0
fi

notify_failure() {
	if [ -n "${NTFY_TOPIC:-}" ]; then
		"$CURL" -fsS -m 10 -d "Playstat MLB daily chain FAILED at $(date '+%F %H:%M')" \
			"https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 ||
			echo "WARN: ntfy failure push did not send"
	else
		echo "WARN: NTFY_TOPIC unset — no failure push sent"
	fi
}

echo "=== chain start $(date '+%F %H:%M:%S') ==="

# Keep the Mac awake for the chain's duration. macOS idle/maintenance sleep was
# suspending the ~1hr morning retrain mid-run (2026-07-18: ~43 min lost to
# maintenance sleep, stretching a 1h job past 2h). caffeinate holds an
# idle+disk-sleep assertion tied to THIS script's PID ($$) and releases the
# moment the script exits — scoped to the run, no sudo, no persistent setting.
# The `-w $$` backgrounded process is reaped when the script ends.
command -v caffeinate >/dev/null && caffeinate -i -m -w $$ 2>/dev/null &

run_chain() {
	if [ -n "${PLAYSTAT_CHAIN_CMD:-}" ]; then
		# Smoke-test hook: stand in for the real pipeline.
		eval "$PLAYSTAT_CHAIN_CMD"
		return $?
	fi
	"$PY" -m ingestion.mlb_backfill --only stats &&
		"$PY" -m ingestion.mlb_backfill --only linescores &&
		"$PY" -m modeling.clv &&
		"$PY" -m modeling.settle &&
		"$PY" -m modeling.features --sport mlb --upcoming-days 2 &&
		"$PY" -m modeling.predict_upcoming --sport mlb --days 2 &&
		"$PY" -m ingestion.odds_ingest --sport mlb &&
		"$PY" -m modeling.first_inning --days 2 &&
		"$PY" -m modeling.edges &&
		"$PY" -m modeling.backtest --sport mlb &&
		"$PY" -m optimizer.parlay --target-payout 2.0 --max-legs 3
}

if run_chain; then
	echo "$today" >"$STATE"
	if [ -n "${HEALTHCHECK_PING_URL:-}" ]; then
		"$CURL" -fsS -m 10 --retry 3 "$HEALTHCHECK_PING_URL" >/dev/null 2>&1 ||
			echo "WARN: heartbeat ping did not send" # a missed ping must not fail the chain
	else
		echo "WARN: HEALTHCHECK_PING_URL unset — no heartbeat ping sent"
	fi
	echo "=== chain OK $(date '+%F %H:%M:%S') ==="
	exit 0
fi

echo "=== chain FAILED $(date '+%F %H:%M:%S') ==="
notify_failure
exit 1
