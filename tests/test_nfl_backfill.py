from ingestion.nfl_backfill import team_points_rows


def test_team_points_rows_played_game_yields_home_and_away():
    row = {"home_score": "27", "away_score": "17"}
    rows = team_points_rows(row, game_id=200_2023_01_5, home_team_id=10, away_team_id=20)
    assert rows == [
        {"team_id": 10, "game_id": 200_2023_01_5, "stat_type": "points", "value": 27},
        {"team_id": 20, "game_id": 200_2023_01_5, "stat_type": "points", "value": 17},
    ]


def test_team_points_rows_unplayed_game_yields_nothing():
    assert team_points_rows({"home_score": "", "away_score": ""}, 1, 10, 20) == []
    assert team_points_rows({}, 1, 10, 20) == []
