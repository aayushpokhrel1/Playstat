from tests.test_soccer_ingest import _FakeConn, _FakeEngine, _FakeClient  # shared fakes


def test_ucl_config_entry():
    from ingestion.config import SPORTS
    assert SPORTS["ucl"]["id_offset"] == 500_000_000
    assert SPORTS["ucl"]["league_id"] == 2
    assert SPORTS["ucl"]["odds_league_id"] == "UEFA_CHAMPIONS_LEAGUE"
    assert SPORTS["ucl"]["base_url"] == "https://v3.football.api-sports.io"


def test_ucl_offset_clears_nfls_real_game_id_band():
    """Guard against the NFL/UCL id collision (README §11). NFL's game_id is
    `200M + season*100000 + week*1000 + ...` (ingestion/nfl_backfill._game_id_map),
    so NFL PHYSICALLY occupies ~402M (season 2023) and climbs +0.1M/season — it
    squats in the 400M band despite its +200M offset label. UCL = 500M + raw
    fixture id must sit clear ABOVE NFL's realistic span. This asserts UCL's base
    exceeds NFL's max id for any season through 2099 (200M + 2099*1e5 + margin).
    """
    from ingestion.config import SPORTS
    nfl_base = SPORTS["nfl"]["id_offset"]                       # 200M (label)
    nfl_max_through_2099 = nfl_base + 2099 * 100_000 + 99_999   # ~409.99M physical
    assert nfl_max_through_2099 > 400_000_000                   # NFL really is in the 400M band
    assert SPORTS["ucl"]["id_offset"] > nfl_max_through_2099    # 500M clears it
    # MLS (+300M) sits below NFL's earliest season (200M + 1999*1e5 = 399.9M) with margin.
    assert SPORTS["mls"]["id_offset"] + 50_000_000 < nfl_base + 1999 * 100_000


def test_soccer_backfill_sport_param_defaults_to_mls(monkeypatch):
    import ingestion.soccer_backfill as sb
    calls = []
    monkeypatch.setattr(sb.db, "upsert", lambda conn, t, c, r: calls.append((t, r)))
    fixtures = [{
        "fixture": {"id": 7, "date": "2024-07-04T00:00:00+00:00", "status": {"short": "FT"}},
        "teams": {"home": {"id": 11, "name": "A"}, "away": {"id": 22, "name": "B"}},
        "goals": {"home": 2, "away": 0},
    }]
    sb.backfill_fixtures(_FakeClient(fixtures, []), _FakeEngine(), 2024)  # no sport arg
    game_row = next(r for t, r in calls if t == "games")
    assert game_row["sport"] == "mls" and game_row["game_id"] == 7 + 300_000_000


def test_soccer_backfill_sport_ucl_uses_offset_and_sport(monkeypatch):
    import ingestion.soccer_backfill as sb
    calls = []
    monkeypatch.setattr(sb.db, "upsert", lambda conn, t, c, r: calls.append((t, r)))
    fixtures = [{
        "fixture": {"id": 7, "date": "2024-09-17T00:00:00+00:00", "status": {"short": "FT"}},
        "teams": {"home": {"id": 11, "name": "Real Madrid"}, "away": {"id": 22, "name": "Milan"}},
        "goals": {"home": 3, "away": 1},
    }]
    sb.backfill_fixtures(_FakeClient(fixtures, []), _FakeEngine(), 2024, sport="ucl")
    game_row = next(r for t, r in calls if t == "games")
    team_row = next(r for t, r in calls if t == "teams")
    assert game_row["sport"] == "ucl" and game_row["game_id"] == 7 + 500_000_000
    assert team_row["sport"] == "ucl" and team_row["team_id"] == 11 + 500_000_000


def test_ucl_stat_and_game_markets():
    from ingestion.odds_ingest import STAT_MAPS, GAME_MARKETS
    # UCL reuses the same soccer statIDs as MLS (same SGO soccer feed).
    assert STAT_MAPS["ucl"] == {
        "shots": "shots", "shots_onGoal": "shots_on_goal", "tackles": "tackles",
    }
    assert GAME_MARKETS["ucl"] == {"full_game_total": ("points", "all", "game")}


def test_ucl_builder_wiring():
    from optimizer.builder import TEAM_MARKETS, SLATE_WINDOW_DAYS, _team_class
    assert TEAM_MARKETS["ucl"] == ("full_game_total",)
    assert SLATE_WINDOW_DAYS["ucl"] == 0
    assert _team_class("ucl") == "game_tier"


def test_ucl_settlement_on_real_2024_stats():
    """The shared soccer settlement path scored against REAL loaded 2022-24 UCL
    rows (verified live 2026-08-05, DB offset +500M). Pure — the actual stat
    values were read from the live DB once and pinned here, so the primitives are
    exercised on real UCL data without a DB in the test. UCL reuses the SAME
    settle_leg / game_total / leg_status as MLS/NFL/NBA (no UCL-specific code).
    """
    from modeling.settle import game_total, leg_status, settle_leg

    # Match total (full_game_total) — game_id 501298986, FT:
    # Bayern München 9 - 2 Dinamo Zagreb (2024-09-17 league phase).
    total = game_total(9, 2)                            # 11.0 goals
    assert total == 11.0
    assert leg_status("FT", total) == "ready"
    assert settle_leg("over", total, 2.5) == "hit"     # over 2.5 goals -> hit
    assert settle_leg("over", total, 11.5) == "miss"   # over 11.5 -> miss
    assert settle_leg("under", total, 11.5) == "hit"   # under 11.5 -> hit

    # Player shots prop — same game, Harry Kane, 8 shots.
    shots = 8
    assert leg_status("FT", shots) == "ready"
    assert settle_leg("over", shots, 2.5) == "hit"     # over 2.5 shots -> hit
    assert settle_leg("over", shots, 8.5) == "miss"    # over 8.5 -> miss
    assert settle_leg("over", shots, 8.0) == "push"    # line == actual -> push

    # An extra-time final (status AET/PEN — present in the loaded UCL data:
    # 17 AET + 16 PEN rows) with a real goal total still settles normally.
    assert leg_status("PEN", game_total(1, 1)) == "ready"
