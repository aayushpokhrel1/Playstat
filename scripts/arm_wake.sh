#!/bin/bash
# Playstat wake-arm — keeps the 17:30 late + 19:45 close jobs reachable.
#
# WHY THIS EXISTS. `pmset repeat` allows exactly ONE repeating wake event, and it
# is already spent on the 08:25 wake the morning chain depends on. The late and
# close jobs deliberately SUPPRESS themselves when they fire outside their window
# (see late_afternoon.sh) because their output is worthless late — so unlike
# daily_chain.sh they cannot recover from a sleeping Mac. There is nothing to
# catch up TO, which means the only fix is for the machine to actually be awake.
#
# Observed 2026-08-11..13: the Mac was away from ~12:00 on 08-11 until ~11:53 on
# 08-13. In that time the designed 17:30/19:45 pulls produced ZERO runs — no
# confirmed-lineup card, no closing snapshot — stalling the §15.9 item 12 CLV
# gate that the entire late-afternoon job exists to feed. Across the whole life
# of the job, `confirmed_lineup` cards exist for exactly ONE day (08-10) and a
# genuine 19:45 close snapshot for exactly ONE day (08-09, the dead Sunday).
#
# So this arms a ONE-OFF `pmset schedule wakeorpoweron` for 17:25, alongside the
# 08:25 repeat. One-off events fire once and expire, hence the daily re-arm.
#
# BEST-EFFORT, and it must NEVER page. Scheduling a power event needs root; this
# uses `sudo -n` (non-interactive) against a narrow NOPASSWD rule. With no rule
# installed, sudo fails, the script says so plainly and exits 0 — a missing wake
# costs a confirmed-lineup card, not an outage. It is never worth blocking a
# launchd job on an auth prompt nobody is there to answer.
#
# LIMITS, stated honestly: a hardware wake only helps a Mac that is asleep and
# present. A laptop that is shut down on a flat battery will not power on, and
# one that is awake in a bag with no network will wake, fetch nothing, and log a
# thin slate. This narrows the gap; it does not close it. Moving the schedule off
# the laptop is the only thing that does.

set -uo pipefail

REPO="${PLAYSTAT_REPO:-/Users/aayushpokhrel/dev/playstat}"
STATE="${PLAYSTAT_WAKE_STATE:-$REPO/logs/.armed_wake}"
PMSET="${PLAYSTAT_PMSET_BIN:-/usr/bin/pmset}"
SUDO="${PLAYSTAT_SUDO_BIN:-/usr/bin/sudo}"

# 17:25 — five minutes ahead of the 17:30 late trigger, so the machine is fully
# awake and back on the network before launchd fires the job. Do not move this
# later without moving the trigger: a wake that lands after 17:30 arrives to a
# job that has already suppressed itself.
ARM_TIME="${PLAYSTAT_WAKE_ARM_TIME:-17:25:00}"

cd "$REPO" || exit 1

# Smoke-test hook, mirroring daily_chain.sh's PLAYSTAT_CHAIN_CMD and
# late_afternoon.sh's PLAYSTAT_LATE_CMD. Without it the only way to exercise
# this script is to mutate the real power schedule, which needs root and changes
# a system setting. With it, every path is testable for free and unprivileged.
_pmset() {
	if [ -n "${PLAYSTAT_PMSET_CMD:-}" ]; then
		eval "$PLAYSTAT_PMSET_CMD"
	else
		"$SUDO" -n "$PMSET" "$@"
	fi
}

# Arm today if 17:25 is still ahead of us, otherwise tomorrow. `date -v+1d` is
# BSD-only, which is correct here — this script is macOS-specific by definition
# (pmset does not exist anywhere else).
arm_hhmm=$((10#$(echo "$ARM_TIME" | tr -d ':' | cut -c1-4)))
now=$((10#$(date +%H%M)))
if [ "$now" -lt "$arm_hhmm" ]; then
	target_date=$(date "+%m/%d/%y")
else
	target_date=$(date -v+1d "+%m/%d/%y")
fi
target="$target_date $ARM_TIME"

prev=""
[ -f "$STATE" ] && prev=$(cat "$STATE" 2>/dev/null)

# Already armed for exactly this instant — do nothing. pmset happily stacks
# duplicate events, and a launchd job with RunAtLoad fires on every wake, so
# without this guard a week of sleep/wake cycles silently accumulates dozens of
# identical scheduled events.
if [ "$prev" = "$target" ]; then
	echo "=== wake-arm: already armed for $target ==="
	exit 0
fi

# Cancel the stale one first. A one-off that has already fired is gone and the
# cancel is a harmless no-op, so this is unconditional and its failure ignored.
if [ -n "$prev" ]; then
	_pmset schedule cancel wakeorpoweron "$prev" >/dev/null 2>&1 &&
		echo "=== wake-arm: cancelled stale $prev ===" ||
		echo "=== wake-arm: no stale event to cancel ($prev) ==="
fi

if _pmset schedule wakeorpoweron "$target"; then
	echo "$target" >"$STATE"
	echo "=== wake-arm: armed wakeorpoweron $target ==="
	exit 0
fi

echo "=== wake-arm: FAILED to arm $target — is the NOPASSWD pmset sudoers rule installed? (best-effort, not an outage) ==="
exit 0
