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


def test_soccer_settlement_on_real_2024_stats():
    """The soccer settlement path scored against REAL loaded 2022-24 MLS rows
    (verified live 2026-08-05, DB offset +300M). Pure — the actual stat values
    are read from the live DB once and pinned here as constants, so the scoring
    primitives are exercised on real data without a DB in the test (no test DB;
    live-DB-in-tests is banned). Both the match-total and player-prop paths
    reuse the existing settle_leg / game_total / leg_status primitives — the
    proof is that they score soccer values correctly with no soccer-specific code.
    """
    from modeling.settle import game_total, leg_status, settle_leg

    # Match total (full_game_total) — game_id 301151050, FT:
    # Columbus Crew 2 - 0 Nashville SC (2024-07-03). team_game_stats('points').
    home_goals, away_goals = 2, 0
    total = game_total(home_goals, away_goals)      # 2.0 goals
    assert total == 2.0
    assert leg_status("FT", total) == "ready"
    assert settle_leg("over", total, 1.5) == "hit"    # over 1.5 goals -> hit
    assert settle_leg("over", total, 2.5) == "miss"   # over 2.5 goals -> miss
    assert settle_leg("under", total, 2.5) == "hit"   # under 2.5 -> hit

    # Player shots prop — game_id 301150755, Cucho Hernández, 5 shots.
    shots = 5
    assert leg_status("FT", shots) == "ready"
    assert settle_leg("over", shots, 2.5) == "hit"    # over 2.5 shots -> hit
    assert settle_leg("over", shots, 5.5) == "miss"   # over 5.5 shots -> miss
    assert settle_leg("over", shots, 5.0) == "push"   # line == actual -> push

    # An extra-time final (status AET/PEN, present in the DB: 2 AET + 16 PEN
    # rows) with a real goal total still settles as a normal match total.
    assert leg_status("AET", game_total(3, 2)) == "ready"
