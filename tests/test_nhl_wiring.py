"""Pure NHL wiring tests — no DB, no network. Import the id helpers, box-score
extractors and status mapping from ingestion.nhl_backfill directly; the
backfill's DB/network paths are covered by the architect's live smoke (Task 5)."""

from ingestion.nhl_backfill import (
    extract_goalie_stats, extract_skater_stats, final_status, nhl_game_id,
    nhl_player_id, nhl_raw_game_id, nhl_team_id, team_full_name,
)


def test_nhl_ids_clear_all_bands_and_fit_int4():
    """Guard test for the +1B id scheme (README §11 / §16.4). NHL native game
    ids are ~2.03e9 and sit above every existing band, so they're stored as
    `1e9 + (raw - 2e9)`. That must land clear of NFL's real ~410M ceiling and
    UCL's 500M while fitting INT4 (2,147,483,647).
    """
    game_id = nhl_game_id(2023020001)
    assert game_id > 410_000_000          # clears NFL's real ~410M span
    assert game_id > 501_000_000          # clears UCL's +500M band
    assert game_id < 2_147_483_647        # fits INT4
    # Teams/players are 1e9 + raw (raw ids are ~7-digit), landing in [1.0e9, 1.1e9).
    assert nhl_team_id(1) == 1_000_000_001
    assert nhl_player_id(1) == 1_000_000_001


def test_nhl_game_id_roundtrip():
    raw = 2025020740  # MTL @ BUF, 2026-01-15 (verified in the plan's first-hour gate)
    assert nhl_raw_game_id(nhl_game_id(raw)) == raw
    # refetch formula from the plan: stored id + 1B == raw.
    assert nhl_raw_game_id(nhl_game_id(raw)) == nhl_game_id(raw) + 1_000_000_000


def test_extract_skater_stats():
    skater = {"sog": 5, "goals": 1, "assists": 0, "points": 1, "hits": 2, "blockedShots": 0, "pim": 2}
    stats = extract_skater_stats(skater)
    assert stats["shots_on_goal"] == 5
    assert stats["goals"] == 1
    assert stats["assists"] == 0          # zeros are real outcomes — kept
    assert stats["points"] == 1
    assert stats["hits"] == 2
    assert stats["blocked_shots"] == 0    # zeros are real outcomes — kept
    assert stats["pim"] == 2


def test_extract_goalie_stats():
    goalie = {"saves": 22, "shotsAgainst": 26, "goalsAgainst": 4, "toi": "58:08"}
    stats = extract_goalie_stats(goalie)
    assert stats["saves"] == 22
    assert stats["shots_against"] == 26
    assert stats["goals_against"] == 4
    # A bench goalie who never entered the game is skipped (None per the extractor contract).
    assert extract_goalie_stats({"saves": 0, "shotsAgainst": 0, "toi": "00:00"}) is None


def test_final_status():
    assert final_status({"gameState": "OFF"}) == "FT"
    assert final_status({"gameState": "FINAL"}) == "FT"
    assert final_status({"gameState": "LIVE"}) == "LIVE"
    assert final_status({"gameState": "FUT"}) == "FUT"
    # All finals (REG/OT/SO) report 'OFF'/'FINAL' — all map to a single 'FT'.
    assert final_status({"gameState": "FINAL", "gameOutcome": {"lastPeriodType": "SO"}}) == "FT"


def test_team_name_from_feed():
    # placeName.default + commonName.default from the schedule feed.
    assert team_full_name("New York", "Rangers") == "New York Rangers"
    assert team_full_name("St. Louis", "Blues") == "St. Louis Blues"
    assert team_full_name("Montréal", "Canadiens") == "Montréal Canadiens"


def test_nhl_maps_present():
    """NHL is registered in every per-sport map the builder + odds paths read."""
    from ingestion.odds_ingest import GAME_MARKETS, STAT_MAPS
    from optimizer.builder import SLATE_WINDOW_DAYS, TEAM_MARKETS, _team_class

    assert STAT_MAPS["nhl"] == {"shots_onGoal": "shots_on_goal", "saves": "saves"}
    assert GAME_MARKETS["nhl"] == {"full_game_total": ("points", "all", "game")}
    assert TEAM_MARKETS["nhl"] == ("full_game_total",)
    assert SLATE_WINDOW_DAYS["nhl"] == 0
    assert _team_class("nhl") == "game_tier"


def test_nhl_stat_map_targets_are_backfill_stats():
    """Every STAT_MAPS['nhl'] target must be a stat_type nhl_backfill actually
    emits, or the prop would ingest a line that can never settle (no actual)."""
    from ingestion.odds_ingest import STAT_MAPS

    emitted = set(extract_skater_stats(
        {"sog": 1, "goals": 0, "assists": 0, "points": 0, "hits": 0, "blockedShots": 0, "pim": 0}
    )) | set(extract_goalie_stats(
        {"saves": 1, "shotsAgainst": 1, "goalsAgainst": 0, "toi": "10:00"}
    ))
    assert set(STAT_MAPS["nhl"].values()) <= emitted
