"""Confirmed MLB lineups + first-pitch times from statsapi (free, no key).

README §15.9 item 11 Option B. The morning chain builds at ~08:39 ET but MLB
lineups post ~2-3h before first pitch, so ~16.2% of player legs still void even
after the Option A start-rate filter. This module supplies the posted lineup so a
17:30 ET pass can build a higher-confidence card.

It returns start times as well as lineups because `games` has NO start-time
column (game_id, date, home_team_id, away_team_id, status, sport) — so "games not
yet started" has no other source, and the same call already carries it.

Verified live 2026-08-08: 15/15 games populate (9 players/side) and 270/270
distinct lineup player ids map to `players` at 100% via the mlb offset.
"""

from datetime import datetime

import requests

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_ID_OFFSET = 100_000_000
TIMEOUT = (10, 30)  # (connect, read) — bare timeouts stalled the chain, §15.9 item 8


def _games(payload):
    for date_block in (payload or {}).get("dates", []):
        for game in date_block.get("games", []):
            yield game


def lineup_player_ids(payload):
    """Set of playstat player_ids appearing in any posted lineup.

    A game whose lineup has not posted has no "lineups" key at all; it simply
    contributes nothing rather than raising.
    """
    ids = set()
    for game in _games(payload):
        lineups = game.get("lineups") or {}
        for side in ("homePlayers", "awayPlayers"):
            for player in lineups.get(side) or []:
                pid = player.get("id")
                if pid is not None:
                    ids.add(pid + MLB_ID_OFFSET)
    return ids


def game_start_times(payload):
    """{playstat game_id: tz-aware UTC first pitch}. Games with no gameDate are skipped."""
    times = {}
    for game in _games(payload):
        pk, game_date = game.get("gamePk"), game.get("gameDate")
        if pk is None or not game_date:
            continue
        times[pk + MLB_ID_OFFSET] = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
    return times


def fetch_lineups(date_str, session=None):
    """(player_ids, start_times) for an ET slate date 'YYYY-MM-DD'. One network call."""
    getter = session or requests
    response = getter.get(
        SCHEDULE_URL,
        params={"sportId": 1, "startDate": date_str, "endDate": date_str,
                "hydrate": "lineups"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return lineup_player_ids(payload), game_start_times(payload)
