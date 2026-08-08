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
	# Order matters (reordered 2026-07-24 §15.9; model pipeline SHELVED 2026-07-29
	# §16 — see the frozen block below). The low-risk builder ranks on de-vigged
	# MARKET odds and needs ONLY games + prop_lines + game_lines (model_prob was
	# always a context-only LEFT JOIN, never used for ranking), so it never depended
	# on the model steps — dropping them changes nothing about the card. The four
	# builder --save steps run right after their single ingestion dep, odds_ingest
	# (it writes both prop_lines and the NRFI/F5 game_lines the builder reads).
	# Saved rows carry model_prob=None (dashboard shows "model: — (not used for
	# ranking)") — the `edges`/`game_edges` tables are gone (§16/#3B) so it is
	# always None now. The old `first_inning` step (wrote game_predictions, a
	# now-dropped table read by nothing) was removed 2026-08-06 with the model
	# code — it never fed the builder (a mistaken KEEP in the #3B spec).
	# settle is independent of the builder.
	# Per-step timing (2026-07-24, README §15.9 item 7 B). Profiling ruled out
	# feature-compute (~77s) and model-training (~2s/stat) as the cause of the
	# ~7-8h runtime; the feature UPSERT (2.3M immutable rows) is >10min and a big
	# chunk is still unaccounted (likely the ingestion/API steps). _step logs each
	# step's duration to mlb.log so the next run pinpoints the real bottleneck
	# before we optimize. It preserves && short-circuit: it returns the wrapped
	# command's exit code, so a failing step still stops the chain.
	_step() { local n="$1"; shift; local s; s=$(date +%s); "$@"; local r=$?; echo "=== step $n: $(( $(date +%s) - s ))s rc=$r ==="; return $r; }
	# Network ingestion steps get one delayed re-run: a transient blip (DNS not up
	# yet at boot, a mid-fetch connection drop) can outlast Layer 1's client-level
	# retries but usually clears within two minutes. Compute/model steps below stay
	# on the plain fail-fast _step — auto-re-running a real data bug or a 64-min
	# backtest is wrong.
	_step_retry() { local n="$1"; shift; _step "$n" "$@" && return 0; local r=$?; echo "=== step $n: retrying after ${r} in 120s ==="; sleep 120; _step "$n" "$@"; return $?; }
	# NFL builds ONCE PER WEEK — Thursday only (NFL chain #4a, 2026-07-29-nfl-chain-record
	# spec). NFL bets a weekly Thu..Mon card (builder --sport nfl uses window_days=4);
	# rebuilding that same slate every day would re-save it ~6x and N-count it in the
	# paper ledger, so the BUILD is gated to Thursday. Scores + settle run DAILY below
	# so each game settles as it finishes (TNF->Fri, Sunday->Mon, MNF->Tue). Non-Thursday:
	# skip, return 0. Offseason Thursday: the builder finds no candidate legs, exits 0.
	# Called best-effort (see the chain below): an NFL failure is logged but never
	# aborts the MLB chain or pages — NFL is secondary/seasonal, live at preseason (~Aug).
	# MLB-ONLY odds (2026-08-07, user-chosen SGO free-tier quota conservation — README
	# §15.9 item 3 note). The non-MLB SGO odds pulls drain the shared 2,500-object/mo
	# free budget; they are gated off here so the monthly quota goes to MLB (the only
	# sport reliably producing cards). Reversible with NO code edit: run the chain with
	# PLAYSTAT_MLB_ONLY_ODDS=0 to restore full multi-sport odds. The *_scores steps stay
	# on (non-SGO APIs; they keep settle able to resolve any pending non-MLB parlays).
	_mlb_only() { [ "${PLAYSTAT_MLB_ONLY_ODDS:-1}" = 1 ]; }

	# NBA scores require a PAID API-Sports basketball plan. The free tier caps at
	# seasons 2022-2024, so `--season current` returns a DETERMINISTIC error
	# ("Free plans do not have access to this season, try from 2022 to 2024") —
	# not a transient network fault. Left on _step_retry it burned a pointless
	# 120s sleep + guaranteed-failing re-run EVERY night. Default OFF; nothing
	# depends on it today (NBA builds are already skipped under _mlb_only, so no
	# NBA parlays are created or left pending, and nba `games` data stops at the
	# free-tier cap 2024-06-18). Flip PLAYSTAT_NBA_SCORES=1 — no code edit — the
	# moment a paid plan exists.
	_nba_scores_on() { [ "${PLAYSTAT_NBA_SCORES:-0}" = 1 ]; }
	_nfl_weekly_build() {
		if _mlb_only; then echo "=== nfl build: skipped (MLB-only odds, PLAYSTAT_MLB_ONLY_ODDS) ==="; return 0; fi
		if [ "$(date +%u)" -ne 4 ]; then echo "=== nfl build: skipped (not Thursday) ==="; return 0; fi
		_step_retry nfl_odds  "$PY" -m ingestion.odds_ingest --sport nfl &&
			_step nfl_builder_1.4 "$PY" -m optimizer.builder --sport nfl --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nfl_builder_2.0 "$PY" -m optimizer.builder --sport nfl --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nfl_game_1.4    "$PY" -m optimizer.builder --sport nfl --team-only --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nfl_game_2.0    "$PY" -m optimizer.builder --sport nfl --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save
	}
	# NBA is DAILY (like MLB, no weekly gate) — it plays a single-day slate
	# (SLATE_WINDOW_DAYS nba=0). Build player + game-tier cards daily; scores +
	# settle run daily below. Best-effort (see the chain): an NBA failure is logged
	# but never aborts the MLB chain or pages. Live at season (~October). The score
	# refresh uses --season current so it always targets the live NBA season.
	_nba_daily_build() {
		if _mlb_only; then echo "=== nba build: skipped (MLB-only odds, PLAYSTAT_MLB_ONLY_ODDS) ==="; return 0; fi
		_step_retry nba_odds  "$PY" -m ingestion.odds_ingest --sport nba &&
			_step nba_builder_1.4 "$PY" -m optimizer.builder --sport nba --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nba_builder_2.0 "$PY" -m optimizer.builder --sport nba --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nba_game_1.4    "$PY" -m optimizer.builder --sport nba --team-only --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nba_game_2.0    "$PY" -m optimizer.builder --sport nba --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save
	}
	# MLS (soccer) — DAILY like NBA. Player (shots/tackles) + match-total game
	# tier. Best-effort: an MLS failure logs but never aborts the MLB chain.
	# Live only with a paid API-Sports plan (current-season stats); inert on free.
	_mls_daily_build() {
		if _mlb_only; then echo "=== mls build: skipped (MLB-only odds, PLAYSTAT_MLB_ONLY_ODDS) ==="; return 0; fi
		_step_retry mls_odds  "$PY" -m ingestion.odds_ingest --sport mls &&
			_step mls_builder_1.4 "$PY" -m optimizer.builder --sport mls --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step mls_builder_2.0 "$PY" -m optimizer.builder --sport mls --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step mls_game_1.4    "$PY" -m optimizer.builder --sport mls --team-only --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step mls_game_2.0    "$PY" -m optimizer.builder --sport mls --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save
	}
	# UCL (soccer) — DAILY like MLS. Same shared soccer maps, league 2. Best-effort:
	# a UCL failure logs but never aborts the MLB chain. Live only with a paid
	# API-Sports plan (current-season stats); inert on free.
	_ucl_daily_build() {
		if _mlb_only; then echo "=== ucl build: skipped (MLB-only odds, PLAYSTAT_MLB_ONLY_ODDS) ==="; return 0; fi
		_step_retry ucl_odds  "$PY" -m ingestion.odds_ingest --sport ucl &&
			_step ucl_builder_1.4 "$PY" -m optimizer.builder --sport ucl --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step ucl_builder_2.0 "$PY" -m optimizer.builder --sport ucl --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step ucl_game_1.4    "$PY" -m optimizer.builder --sport ucl --team-only --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step ucl_game_2.0    "$PY" -m optimizer.builder --sport ucl --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save
	}
	# NHL (hockey) — DAILY like NBA. Player (shots-on-goal/saves) + match-total
	# game tier. Best-effort: an NHL failure logs but never aborts the MLB chain.
	# LIVE-FOR-FREE (NHL's own free API + free SGO odds) — unlike NBA/MLS/UCL this
	# needs NO paid plan: it does real work the moment the season opens (~Oct) and
	# is harmlessly inert (0 games -> 0 legs) in the offseason. nhl_scores below
	# defaults --season to the live season (current_nhl_season()).
	_nhl_daily_build() {
		if _mlb_only; then echo "=== nhl build: skipped (MLB-only odds, PLAYSTAT_MLB_ONLY_ODDS) ==="; return 0; fi
		_step_retry nhl_odds  "$PY" -m ingestion.odds_ingest --sport nhl &&
			_step nhl_builder_1.4 "$PY" -m optimizer.builder --sport nhl --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nhl_builder_2.0 "$PY" -m optimizer.builder --sport nhl --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nhl_game_1.4    "$PY" -m optimizer.builder --sport nhl --team-only --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
			_step nhl_game_2.0    "$PY" -m optimizer.builder --sport nhl --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save
	}
	# --- MLB core build (stats -> linescores -> odds -> builders -> multi-sport). May
	# fail on an SGO 429 / quota-out morning; settle runs AFTER this group regardless. ---
	{
	_step_retry stats       "$PY" -m ingestion.mlb_backfill --only stats &&
		_step_retry linescores  "$PY" -m ingestion.mlb_backfill --only linescores &&
		_step_retry odds        "$PY" -m ingestion.odds_ingest --sport mlb --slate-window &&
		_step builder_1.4      "$PY" -m optimizer.builder --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --min-start-rate 0.65 --save &&
		_step builder_2.0      "$PY" -m optimizer.builder --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --min-start-rate 0.65 --save &&
		_step builder_team_1.4 "$PY" -m optimizer.builder --team-only --target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
		_step builder_team_2.0 "$PY" -m optimizer.builder --team-only --target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save &&
		# Same-game NRFI+F5 combos (README §15.9 item 1) — the labelled exception to
		# the across-game-only guardrail. Pins no payout axis (one card per eligible
		# game). Non-fatal: this class is empty most nights (both markets must clear
		# the 0.55 floor in the SAME game), and it must never block the player card.
		{ _step builder_same_game "$PY" -m optimizer.builder --same-game --top-n 5 --save || echo "=== builder_same_game: FAILED (non-fatal) ==="; } &&
		{ _nfl_weekly_build || echo "=== nfl weekly build: FAILED (non-fatal, MLB chain continues) ==="; } &&
		{ _step_retry nfl_scores "$PY" -m ingestion.nfl_backfill --only games || echo "=== nfl_scores: FAILED (non-fatal) ==="; } &&
		{ _nba_daily_build || echo "=== nba daily build: FAILED (non-fatal, MLB chain continues) ==="; } &&
		{ if _nba_scores_on; then
			_step_retry nba_scores "$PY" -m ingestion.backfill --sport nba --only games --season current || echo "=== nba_scores: FAILED (non-fatal) ==="
		  else
			echo "=== nba_scores: skipped (needs a paid API-Sports plan; set PLAYSTAT_NBA_SCORES=1) ==="
		  fi; } &&
		{ _mls_daily_build || echo "=== mls daily build: FAILED (non-fatal, MLB chain continues) ==="; } &&
		{ _step_retry mls_scores "$PY" -m ingestion.soccer_backfill --season 2024 --only fixtures || echo "=== mls_scores: FAILED (non-fatal) ==="; } &&
		{ _ucl_daily_build || echo "=== ucl daily build: FAILED (non-fatal, MLB chain continues) ==="; } &&
		{ _step_retry ucl_scores "$PY" -m ingestion.soccer_backfill --sport ucl --season 2024 --only fixtures || echo "=== ucl_scores: FAILED (non-fatal) ==="; } &&
		{ _nhl_daily_build || echo "=== nhl daily build: FAILED (non-fatal, MLB chain continues) ==="; } &&
		{ _step_retry nhl_scores "$PY" -m ingestion.nhl_backfill --only games || echo "=== nhl_scores: FAILED (non-fatal) ==="; } &&
		# Kelly stake sizing (README §15.9 item 4): size the WHOLE date's builder
		# rows once all sport builds are done, before settle. ¼-Kelly on the
		# line-shopping edge + a 5u same-night exposure cap; idempotent. NULL stays
		# on any card built after this (none, in MLB-only) -> settle falls back to 1u.
		_step stake            "$PY" -m optimizer.stake
	}
	core_rc=$?
	# settle is INDEPENDENT of odds/builders — it reads finished-game stats already in
	# the DB — so it MUST run even when the MLB odds pull 429s or a build fails. Before
	# 2026-08-07 settle sat at the tail of the core && chain, so an odds 429 short-
	# circuited the chain and settle never ran, starving the paper ledger on a quota-out
	# morning. Now it runs unconditionally; the chain still FAILS (pages) iff the core
	# build OR settle failed, so an empty-slate morning is still surfaced.
	_step settle           "$PY" -m modeling.settle
	settle_rc=$?
	[ "$core_rc" -eq 0 ] && [ "$settle_rc" -eq 0 ]
	# MODEL PIPELINE DELETED 2026-08-06 (README §16, roadmap #3B, user-approved).
	# The model was shelved 2026-07-29 (frozen steps commented out here) and then
	# DELETED — the four steps (features/predict_upcoming/edges/backtest) and their
	# modules/tables are gone, along with the clv step. The market-ranked builder
	# never depended on them (model_prob was a context-only LEFT JOIN, now always
	# None), so the chain above (odds -> builders -> multi-sport -> settle) is
	# unchanged. first_inning was removed too (dead: wrote the dropped
	# game_predictions, fed nothing). The /edges, /game-predictions and
	# /parlay-recommendations endpoints were removed (#3A). To resurrect the model,
	# recover the deleted modules from git history (pre-3fb41e0 commits).
}
# The old `optimizer.parlay --target-payout 2.0 --max-legs 3` step lived here and
# OOM-died (SIGKILL) nightly — 1,060 edges > 3% meant C(1060,3) ~ 198M combinations
# (README §11). It is replaced, not patched, by optimizer.builder: a bounded,
# game-structured search that ranks on de-vigged MARKET probability rather than
# model probability. Two targets are recorded each night so the paper ledger
# accumulates at both risk levels — ~1.4x "safe" and ~2.0x "reach" (README §15.3).
# Tolerance is tightened to 0.10 (default 0.15) because ranking by joint
# probability always returns the least-risky end of the band, so a wide band
# records a bet well below its nominal target (README §15.10).
#
# The two --team-only builds add a dedicated, separately-tracked team tier
# (README §15.9 item 5 / §15.10 team-legs note): NRFI/F5 markets price near
# coin-flip and are structurally out-competed by player-prop favorites in the
# mixed pool above, so team legs almost never surfaced there. This tier can
# legitimately find nothing on a given slate (`optimizer.builder` prints "no
# candidate legs" and returns normally, exit 0) — that is expected, not a
# chain failure.

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
