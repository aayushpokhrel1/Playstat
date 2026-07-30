from ingestion.backfill import is_final, nba_team_points_rows


def test_is_final_recognizes_ft_and_aot():
    assert is_final("FT") is True
    assert is_final("AOT") is True
    assert is_final("NS") is False
    assert is_final("S") is False
    assert is_final(None) is False


def test_nba_team_points_rows_scored_game():
    game = {"scores": {"home": {"total": 112}, "away": {"total": 108}}}
    rows = nba_team_points_rows(game, game_id=500, home_team_id=10, away_team_id=20)
    assert rows == [
        {"team_id": 10, "game_id": 500, "stat_type": "points", "value": 112},
        {"team_id": 20, "game_id": 500, "stat_type": "points", "value": 108},
    ]


def test_nba_team_points_rows_missing_score_returns_empty():
    game = {"scores": {"home": {"total": None}, "away": {"total": 108}}}
    assert nba_team_points_rows(game, game_id=1, home_team_id=1, away_team_id=2) == []
    assert nba_team_points_rows({}, game_id=1, home_team_id=1, away_team_id=2) == []


def test_current_nba_season_labels_by_start_year():
    from datetime import date
    from ingestion.backfill import current_nba_season
    assert current_nba_season(date(2026, 10, 25)) == "2026-2027"
    assert current_nba_season(date(2027, 4, 10)) == "2026-2027"
    assert current_nba_season(date(2026, 7, 30)) == "2025-2026"


def test_nba_game_markets_shape():
    from ingestion.odds_ingest import GAME_MARKETS, bettype_for_market
    assert GAME_MARKETS["nba"] == {
        "full_game_total": ("points", "all", "game"),
        "full_game_spread": ("points", "all", "game"),
        "full_game_moneyline": ("points", "all", "game"),
    }
    assert bettype_for_market("full_game_spread") == "sp"
    assert bettype_for_market("full_game_moneyline") == "ml"
    assert bettype_for_market("full_game_total") == "ou"


def test_nba_team_markets_and_geometry():
    from optimizer.builder import TEAM_MARKETS
    from optimizer.builder_core import MARKET_GEOMETRY
    assert TEAM_MARKETS["nba"] == (
        "full_game_total", "full_game_spread", "full_game_moneyline",
    )
    assert MARKET_GEOMETRY["full_game_total"] == "ou"
    assert MARKET_GEOMETRY["full_game_spread"] == "homeaway"
    assert MARKET_GEOMETRY["full_game_moneyline"] == "homeaway"


def test_nba_daily_cadence_and_game_tier_class():
    from optimizer.builder import SLATE_WINDOW_DAYS, _team_class
    assert SLATE_WINDOW_DAYS["nba"] == 0          # daily like MLB, not NFL's weekly 4
    assert _team_class("nba") == "game_tier"       # shared with NFL
    assert _team_class("nfl") == "game_tier"
    assert _team_class("mlb") == "team_tier"       # unchanged


def test_aot_overtime_game_settles_as_final():
    # NBA over-time finals come in as "AOT", not "FT". They MUST settle, not
    # strand pending forever (caught in NBA build verification 2026-07-30).
    from modeling.settle import leg_status
    assert leg_status("AOT", 100) == "ready"       # final + stat present
    assert leg_status("FT", 100) == "ready"        # regulation final unchanged
    assert leg_status("AOT", None) == "void"       # final but no stat -> void, not pending
    assert leg_status("NS", None) == "pending"     # not started
    assert leg_status("S", 100) == "pending"       # scheduled


def test_nba_game_tier_scoring_on_real_final_score():
    # Real backfilled NBA game 372186 (2023-2024): home 111, away 99 (margin +12).
    from modeling.settle import game_total, settle_spread_leg, settle_moneyline_leg, settle_leg
    home, away = 111, 99
    assert game_total(home, away) == 210.0
    # moneyline: home won by 12
    assert settle_moneyline_leg("home", home, away) == "won"
    assert settle_moneyline_leg("away", home, away) == "lost"
    # spread: home -5.5 covered (12 - 5.5 = 6.5 > 0); away +5.5 did not
    assert settle_spread_leg("home", home, away, -5.5) == "won"
    assert settle_spread_leg("away", home, away, -5.5) == "lost"
    # total 210 vs line 208.5: over hits, under misses
    assert settle_leg("over", 210.0, 208.5) == "hit"
    assert settle_leg("under", 210.0, 208.5) == "miss"
