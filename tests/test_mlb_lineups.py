from datetime import timezone

import pytest

from ingestion.mlb_lineups import game_start_times, lineup_player_ids

# Trimmed shape of statsapi /api/v1/schedule?hydrate=lineups.
PAYLOAD = {
    "dates": [
        {
            "date": "2026-08-08",
            "games": [
                {
                    "gamePk": 824085,
                    "gameDate": "2026-08-08T22:40:00Z",
                    "lineups": {
                        "homePlayers": [{"id": 11}, {"id": 12}],
                        "awayPlayers": [{"id": 13}],
                    },
                },
                {
                    # Lineups not posted yet: the key is absent entirely.
                    "gamePk": 824086,
                    "gameDate": "2026-08-09T01:50:00Z",
                },
            ],
        }
    ]
}


def test_lineup_player_ids_applies_the_mlb_offset():
    assert lineup_player_ids(PAYLOAD) == {100_000_011, 100_000_012, 100_000_013}


def test_lineup_player_ids_tolerates_games_without_posted_lineups():
    # Must not raise; the unposted game simply contributes nothing.
    assert 824086 + 100_000_000 not in lineup_player_ids(PAYLOAD)


def test_lineup_player_ids_empty_payload():
    assert lineup_player_ids({}) == set()


def test_game_start_times_applies_offset_and_returns_utc():
    times = game_start_times(PAYLOAD)
    assert set(times) == {100_824_085, 100_824_086}
    assert times[100_824_085].tzinfo is not None
    assert times[100_824_085].astimezone(timezone.utc).hour == 22


def test_game_start_times_skips_games_missing_a_date():
    payload = {"dates": [{"games": [{"gamePk": 1}]}]}
    assert game_start_times(payload) == {}
