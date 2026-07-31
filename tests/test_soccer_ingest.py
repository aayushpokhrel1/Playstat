from ingestion.soccer_backfill import (
    is_soccer_final, soccer_team_points_rows, extract_soccer_player_stats,
)


def test_is_soccer_final():
    assert is_soccer_final("FT") is True
    assert is_soccer_final("AET") is True   # after extra time
    assert is_soccer_final("PEN") is True   # penalty shootout
    assert is_soccer_final("NS") is False   # not started
    assert is_soccer_final("1H") is False   # in play
    assert is_soccer_final(None) is False


def test_soccer_team_points_rows_scored():
    fixture = {"goals": {"home": 2, "away": 1}}
    rows = soccer_team_points_rows(fixture, game_id=900, home_team_id=50, away_team_id=60)
    assert rows == [
        {"team_id": 50, "game_id": 900, "stat_type": "points", "value": 2},
        {"team_id": 60, "game_id": 900, "stat_type": "points", "value": 1},
    ]


def test_soccer_team_points_rows_missing_returns_empty():
    assert soccer_team_points_rows({"goals": {"home": None, "away": 1}}, 1, 1, 2) == []
    assert soccer_team_points_rows({}, 1, 1, 2) == []


def test_extract_soccer_player_stats():
    block = {
        "games": {"minutes": 90},
        "shots": {"total": 3, "on": 1},
        "tackles": {"total": 4, "blocks": 0, "interceptions": 2},
        "passes": {"total": 55, "key": 2, "accuracy": "88"},
    }
    assert extract_soccer_player_stats(block) == {
        "shots": 3, "shots_on_goal": 1, "tackles": 4,
    }


def test_extract_soccer_player_stats_drops_none():
    # a goalkeeper: shots/tackles null
    block = {"shots": {"total": None, "on": None}, "tackles": {"total": None}}
    assert extract_soccer_player_stats(block) == {}


class _FakeConn:
    def __init__(self): self.upserts = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): return []

class _FakeEngine:
    def __init__(self): self.conn = _FakeConn()
    def begin(self): return self.conn

class _FakeClient:
    """Returns queued /fixtures then /fixtures/players payloads."""
    def __init__(self, fixtures, players): self._fx, self._pl = fixtures, players
    def get(self, path, params=None):
        return self._fx if path == "/fixtures" else self._pl


def test_backfill_fixtures_upserts_games_teams_scores(monkeypatch):
    import ingestion.soccer_backfill as sb
    calls = []
    monkeypatch.setattr(sb.db, "upsert", lambda conn, t, c, r: calls.append((t, r)))
    fixtures = [{
        "fixture": {"id": 7, "date": "2024-07-04T00:00:00+00:00", "status": {"short": "FT"}},
        "teams": {"home": {"id": 11, "name": "Columbus Crew"},
                  "away": {"id": 22, "name": "Nashville SC"}},
        "goals": {"home": 2, "away": 0},
    }]
    client = _FakeClient(fixtures, [])
    finished = sb.backfill_fixtures(client, _FakeEngine(), 2024)
    off = sb.SPORTS["mls"]["id_offset"]
    tables = [t for t, _ in calls]
    assert tables.count("teams") == 2
    assert tables.count("games") == 1
    assert tables.count("team_game_stats") == 2  # final -> two score rows
    game_row = next(r for t, r in calls if t == "games")
    assert game_row["game_id"] == 7 + off and game_row["sport"] == "mls"
    assert len(finished) == 1


def test_backfill_fixtures_skips_scores_for_unfinished(monkeypatch):
    import ingestion.soccer_backfill as sb
    calls = []
    monkeypatch.setattr(sb.db, "upsert", lambda conn, t, c, r: calls.append((t, r)))
    fixtures = [{
        "fixture": {"id": 8, "date": "2024-08-01T00:00:00+00:00", "status": {"short": "NS"}},
        "teams": {"home": {"id": 11, "name": "A"}, "away": {"id": 22, "name": "B"}},
        "goals": {"home": None, "away": None},
    }]
    finished = sb.backfill_fixtures(_FakeClient(fixtures, []), _FakeEngine(), 2024)
    assert [t for t, _ in calls].count("team_game_stats") == 0
    assert finished == []
