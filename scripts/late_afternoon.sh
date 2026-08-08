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

_step() { local n="$1"; shift; local s; s=$(date +%s); "$@"; local r=$?; echo "=== step $n: $(( $(date +%s) - s ))s rc=$r ==="; return $r; }

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
