from tests.test_soccer_ingest import _FakeConn, _FakeEngine, _FakeClient  # shared fakes


def test_ucl_config_entry():
    from ingestion.config import SPORTS
    assert SPORTS["ucl"]["id_offset"] == 400_000_000
    assert SPORTS["ucl"]["league_id"] == 2
    assert SPORTS["ucl"]["odds_league_id"] == "UEFA_CHAMPIONS_LEAGUE"
    assert SPORTS["ucl"]["base_url"] == "https://v3.football.api-sports.io"


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
    assert game_row["sport"] == "ucl" and game_row["game_id"] == 7 + 400_000_000
    assert team_row["sport"] == "ucl" and team_row["team_id"] == 11 + 400_000_000


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
