#!/bin/bash
# Playstat late-afternoon job — README §15.9 item 11 Option B + item 12.
#
# Two launchd triggers share this script:
#   com.playstat.mlb.late  17:30 ET  (full: odds -> confirmed-lineup builds)
#   com.playstat.mlb.close 19:45 ET  (--odds-only: the closing snapshot)
#
# BEST-EFFORT BY DESIGN: it must never page. The morning card has already landed
# and IS the product; a missed confirmed card is a missed improvement, not an
# outage. Failures go to logs/mlb.log and the script exits non-zero quietly.
#
# QUOTA: every pull is --slate-window --not-before-now, so it fetches only
# not-yet-started games in today's ET slate. Measured ~9.6 entities at 17:30 and
# ~3.7 at 19:45 against a 2,500/month cap; all three daily pulls together cost
# ~27/day, roughly HALF what the single unfiltered morning pull used to cost.
# --not-before-now also keeps in-play prices out of a pre-game snapshot.
#
# 17:30 sits inside a measured structural gap: across 193 games / 14 days, NO
# MLB game starts between 16:10 and 18:05 ET, so the trigger costs zero coverage
# versus the originally scoped 16:45 while landing 45 min closer to the close.

set -uo pipefail

REPO="${PLAYSTAT_REPO:-/Users/aayushpokhrel/dev/playstat}"
PY="$REPO/.venv/bin/python"

cd "$REPO" || exit 1

# launchd does not read .env; the python modules load it via dotenv themselves,
# but this wrapper's own conditionals need it too.
if [ -f "$REPO/.env" ]; then
	set -a
	# shellcheck disable=SC1091
	. "$REPO/.env"
	set +a
fi

ODDS_ONLY=0
[ "${1:-}" = "--odds-only" ] && ODDS_ONLY=1

# FRESHNESS WINDOW — SUPPRESS a stale run, do NOT retry it.
#
# This is the opposite of daily_chain.sh's catch-up. That job's output (the
# morning card) is still useful hours late, so it re-runs. THIS job's output is
# worthless late: a "confirmed lineup" card for games already underway, or a
# "closing" snapshot taken after the final out. There is nothing to catch up to.
#
# launchd fires a missed StartCalendarInterval on WAKE. Observed 2026-08-11: the
# 08-10 19:45 close job fired at 06:08 the next morning, which (a) captured no
# closing line at all and (b) burned ~15 SGO entities re-pulling the NEW day's
# slate that the 08:39 chain then pulled again 2.5h later — a wasted pull and a
# stray snapshot. Outside its window the job now logs and exits 0 (0 = "nothing
# to do", not failure; this job is best-effort and must never page).
if [ "$ODDS_ONLY" = 1 ]; then
	WINDOW_OPEN="${PLAYSTAT_CLOSE_WINDOW_OPEN:-1945}"
	WINDOW_CLOSE="${PLAYSTAT_CLOSE_WINDOW_CLOSE:-2230}"
	WINDOW_NAME="close"
else
	WINDOW_OPEN="${PLAYSTAT_LATE_WINDOW_OPEN:-1730}"
	WINDOW_CLOSE="${PLAYSTAT_LATE_WINDOW_CLOSE:-1900}"
	WINDOW_NAME="late"
fi
now=$((10#$(date +%H%M))) # 10# forces base 10: "0608" is not a valid octal literal
if [ "$now" -lt "$((10#$WINDOW_OPEN))" ] || [ "$now" -ge "$((10#$WINDOW_CLOSE))" ]; then
	echo "=== late-afternoon ($WINDOW_NAME): SKIPPED at $(date '+%F %H:%M:%S') — outside ${WINDOW_OPEN}-${WINDOW_CLOSE}; a late run cannot produce a pre-game card or a closing line ==="
	exit 0
fi

# Smoke-test hook, mirroring daily_chain.sh's PLAYSTAT_CHAIN_CMD. Without it the
# only way to exercise the window guard's PASS path is to run the real pull,
# which spends SGO entities against a metered free tier (done accidentally once,
# 2026-08-11: 15 entities). With it, both guard paths are testable for free.
_step() {
	local n="$1"; shift
	local s; s=$(date +%s)
	if [ -n "${PLAYSTAT_LATE_CMD:-}" ]; then eval "$PLAYSTAT_LATE_CMD"; else "$@"; fi
	local r=$?
	echo "=== step $n: $(( $(date +%s) - s ))s rc=$r ==="
	return $r
}

echo "=== late-afternoon start $(date '+%F %H:%M:%S') (odds_only=$ODDS_ONLY) ==="

# Hold off idle sleep for the run; released when this script exits.
command -v caffeinate >/dev/null && caffeinate -i -m -w $$ 2>/dev/null &

_step odds_late "$PY" -m ingestion.odds_ingest --sport mlb --slate-window --not-before-now
rc=$?

if [ "$ODDS_ONLY" = 1 ]; then
	echo "=== late-afternoon done (odds only) rc=$rc $(date '+%F %H:%M:%S') ==="
	exit $rc
fi

if [ "$rc" -ne 0 ]; then
	echo "=== late-afternoon: odds failed rc=$rc — skipping builds (non-fatal) ==="
	exit $rc
fi

# Confirmed-lineup builds. NOTE: deliberately NO --min-start-rate here. A posted
# lineup is direct evidence of starting; the 0.65 start-rate proxy the morning
# chain uses would drop confirmed starters with thin history for no reason.
_step confirmed_1.4 "$PY" -m optimizer.builder --require-confirmed-lineup \
	--target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save ||
	echo "=== confirmed_1.4: FAILED (non-fatal) ==="

_step confirmed_2.0 "$PY" -m optimizer.builder --require-confirmed-lineup \
	--target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save ||
	echo "=== confirmed_2.0: FAILED (non-fatal) ==="

echo "=== late-afternoon done $(date '+%F %H:%M:%S') ==="
