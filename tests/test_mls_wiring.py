def test_mls_stat_and_game_markets():
    from ingestion.odds_ingest import STAT_MAPS, GAME_MARKETS
    assert STAT_MAPS["mls"] == {
        "shots": "shots", "shots_onGoal": "shots_on_goal", "tackles": "tackles",
    }
    # match total reuses the existing full_game_total market name (zero new plumbing)
    assert GAME_MARKETS["mls"] == {"full_game_total": ("points", "all", "game")}


def test_mls_builder_wiring():
    from optimizer.builder import TEAM_MARKETS, SLATE_WINDOW_DAYS, _team_class
    from optimizer.builder_core import MARKET_GEOMETRY
    assert TEAM_MARKETS["mls"] == ("full_game_total",)
    assert SLATE_WINDOW_DAYS["mls"] == 0            # daily like MLB/NBA
    assert _team_class("mls") == "game_tier"
    assert MARKET_GEOMETRY["full_game_total"] == "ou"  # reused, no edit needed


def test_soccer_extra_time_settles_as_final():
    from modeling.settle import leg_status
    assert leg_status("AET", 3) == "ready"   # after extra time -> final
    assert leg_status("PEN", 2) == "ready"   # penalty shootout -> final
    assert leg_status("AET", None) == "void" # final but no stat -> void
    assert leg_status("HT", 1) == "pending"  # half time -> not final
    # existing finals unchanged
    assert leg_status("FT", 1) == "ready"
    assert leg_status("AOT", 1) == "ready"
