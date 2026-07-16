"""Unit tests for the multi-sport ID mapping scheme:
  - ingestion/config.py's per-sport id_offset (nba/mlb/nfl namespacing)
  - ingestion/nfl_backfill.py's canonical team/game/player ID derivation

No DB access. The root conftest.py supplies dummy DATABASE_URL/
API_BASKETBALL_KEY so ingestion.config can be imported without a .env.

Run with: python -m pytest tests/test_ids.py -q
"""

import pytest

from ingestion.config import SPORTS
from ingestion.nfl_backfill import (
    CANONICAL_TEAMS,
    NFL_ID_OFFSET,
    TEAM_INDEX,
    _game_id_map,
    _player_id,
    _team_id_map,
    _team_index,
)


# --- ingestion/config.py: per-sport id_offset ---------------------------------

def test_sports_offsets_match_documented_values():
    assert SPORTS["nba"]["id_offset"] == 0
    assert SPORTS["mlb"]["id_offset"] == 100_000_000
    assert SPORTS["nfl"]["id_offset"] == 200_000_000


def test_sport_id_ranges_disjoint_for_plausible_raw_ids():
    # Plausible raw provider IDs are small (well under a few million) for all
    # three sports' providers (API-Sports numeric IDs, MLB StatsAPI IDs,
    # nflverse's derived canonical indices/season-based composites). As long
    # as raw IDs stay under the 100M offset gap, the three sports' namespaced
    # ID ranges can never collide.
    plausible_raw_id_ceiling = 10_000_000  # generous upper bound
    assert plausible_raw_id_ceiling < SPORTS["mlb"]["id_offset"]

    nba_range = (SPORTS["nba"]["id_offset"], SPORTS["nba"]["id_offset"] + plausible_raw_id_ceiling)
    mlb_range = (SPORTS["mlb"]["id_offset"], SPORTS["mlb"]["id_offset"] + plausible_raw_id_ceiling)
    nfl_range = (SPORTS["nfl"]["id_offset"], SPORTS["nfl"]["id_offset"] + plausible_raw_id_ceiling)

    def disjoint(a, b):
        return a[1] < b[0] or b[1] < a[0]

    assert disjoint(nba_range, mlb_range)
    assert disjoint(mlb_range, nfl_range)
    assert disjoint(nba_range, nfl_range)


# --- ingestion/nfl_backfill.py: canonical team index ---------------------------

def test_team_index_is_deterministic_and_1_based():
    assert _team_index("ARI") == 1  # first entry, sorted
    assert TEAM_INDEX["ARI"] == 1
    # every canonical team maps to a unique 1..32 index
    indices = sorted(TEAM_INDEX.values())
    assert indices == list(range(1, len(CANONICAL_TEAMS) + 1))


def test_team_index_raises_clear_error_on_relocation_alias():
    # OAK/SD/STL are pre-2016 relocation aliases deliberately unsupported by
    # this path (module docstring, README §13.1).
    for alias in ("OAK", "SD", "STL"):
        with pytest.raises(ValueError, match="relocation"):
            _team_index(alias)


def test_team_id_map_is_deterministic():
    schedule_rows = [
        {"season": "2023", "home_team": "KC", "away_team": "BUF"},
        {"season": "2023", "home_team": "SF", "away_team": "DAL"},
    ]
    ids1 = _team_id_map(schedule_rows, ["2023"])
    ids2 = _team_id_map(schedule_rows, ["2023"])
    assert ids1 == ids2
    assert ids1["KC"] == NFL_ID_OFFSET + _team_index("KC")


def test_team_id_map_is_set_independent():
    # A team's id must not depend on which other rows/seasons happen to be in
    # the pulled schedule slice — only on its fixed position in CANONICAL_TEAMS.
    rows_full = [
        {"season": "2023", "home_team": "KC", "away_team": "BUF"},
        {"season": "2023", "home_team": "SF", "away_team": "DAL"},
        {"season": "2024", "home_team": "MIA", "away_team": "NYJ"},
    ]
    rows_partial = [
        {"season": "2023", "home_team": "KC", "away_team": "BUF"},
    ]
    ids_full = _team_id_map(rows_full, ["2023", "2024"])
    ids_partial = _team_id_map(rows_partial, ["2023"])
    assert ids_full["KC"] == ids_partial["KC"]
    assert ids_full["BUF"] == ids_partial["BUF"]


# --- ingestion/nfl_backfill.py: game ID scheme ---------------------------------

def _sched_row(season, week, game_id, home_team, away_team):
    return {
        "season": season,
        "week": week,
        "game_id": game_id,
        "home_team": home_team,
        "away_team": away_team,
    }


def test_game_id_map_is_deterministic():
    rows = [_sched_row("2023", "1", "2023_01_BUF_KC", "KC", "BUF")]
    m1 = _game_id_map(rows, ["2023"])
    m2 = _game_id_map(rows, ["2023"])
    assert m1 == m2
    expected = NFL_ID_OFFSET + 2023 * 100_000 + 1 * 1_000 + _team_index("KC")
    assert m1[("2023", "1", "2023_01_BUF_KC")] == expected


def test_game_id_map_is_set_independent():
    # The whole point of home-team-indexed game IDs (vs. a rank within the
    # week's game set): a cancellation or a differently-ordered pull must not
    # shift any other game's ID.
    rows_full = [
        _sched_row("2023", "1", "g1", "KC", "BUF"),
        _sched_row("2023", "1", "g2", "SF", "DAL"),
        _sched_row("2023", "1", "g3", "MIA", "NYJ"),
    ]
    rows_missing_one = [
        _sched_row("2023", "1", "g1", "KC", "BUF"),
        _sched_row("2023", "1", "g3", "MIA", "NYJ"),
        # g2 (SF-DAL) dropped, simulating a cancellation/partial pull
    ]
    full_map = _game_id_map(rows_full, ["2023"])
    partial_map = _game_id_map(rows_missing_one, ["2023"])
    assert full_map[("2023", "1", "g1")] == partial_map[("2023", "1", "g1")]
    assert full_map[("2023", "1", "g3")] == partial_map[("2023", "1", "g3")]


def test_game_ids_unique_within_season_week():
    rows = [
        _sched_row("2023", "1", "g1", "KC", "BUF"),
        _sched_row("2023", "1", "g2", "SF", "DAL"),
        _sched_row("2023", "1", "g3", "MIA", "NYJ"),
        _sched_row("2023", "1", "g4", "GB", "CHI"),
    ]
    game_map = _game_id_map(rows, ["2023"])
    ids = list(game_map.values())
    assert len(ids) == len(set(ids))


# --- ingestion/nfl_backfill.py: player ID scheme -------------------------------

def test_player_id_is_deterministic():
    pid1 = _player_id("00-0033873")
    pid2 = _player_id("00-0033873")
    assert pid1 == pid2
    assert pid1 == NFL_ID_OFFSET + 33873


def test_player_id_uses_trailing_digits():
    assert _player_id("00-0012345") == NFL_ID_OFFSET + 12345
    assert _player_id("01-0000001") == NFL_ID_OFFSET + 1
