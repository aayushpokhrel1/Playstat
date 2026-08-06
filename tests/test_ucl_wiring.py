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
