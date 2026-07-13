from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from api.schemas import GameLogEntry, ModelPerformanceOut, PlayerOut, PredictionOut, TeamOut
from ingestion.db import get_engine

app = FastAPI(title="Playstat API")
engine = get_engine()


@app.get("/teams", response_model=list[TeamOut])
def list_teams():
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT team_id, name FROM teams ORDER BY name")).fetchall()
    return [TeamOut(team_id=r[0], name=r[1]) for r in rows]


@app.get("/players", response_model=list[PlayerOut])
def list_players(team_id: int | None = None):
    query = "SELECT player_id, name, team_id, position FROM players"
    params = {}
    if team_id is not None:
        query += " WHERE team_id = :team_id"
        params["team_id"] = team_id
    query += " ORDER BY name"

    with engine.begin() as conn:
        rows = conn.execute(text(query), params).fetchall()
    return [PlayerOut(player_id=r[0], name=r[1], team_id=r[2], position=r[3]) for r in rows]


@app.get("/players/{player_id}", response_model=PlayerOut)
def get_player(player_id: int):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT player_id, name, team_id, position FROM players WHERE player_id = :player_id"),
            {"player_id": player_id},
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return PlayerOut(player_id=row[0], name=row[1], team_id=row[2], position=row[3])


@app.get("/players/{player_id}/stats", response_model=list[GameLogEntry])
def player_stats(player_id: int, limit: int = 20):
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT pgs.game_id, g.date, pgs.points, pgs.rebounds, pgs.assists, pgs.minutes
                FROM player_game_stats pgs
                JOIN games g ON g.game_id = pgs.game_id
                WHERE pgs.player_id = :player_id
                ORDER BY g.date DESC
                LIMIT :limit
                """
            ),
            {"player_id": player_id, "limit": limit},
        ).fetchall()
    return [
        GameLogEntry(game_id=r[0], date=str(r[1]), points=r[2], rebounds=r[3], assists=r[4], minutes=r[5])
        for r in rows
    ]


@app.get("/players/{player_id}/predictions", response_model=list[PredictionOut])
def player_predictions(player_id: int, stat: str | None = None):
    query = """
        SELECT mp.game_id, g.date, mp.stat_type, mp.predicted_mean, mp.predicted_std, mp.model_version,
               CASE mp.stat_type
                   WHEN 'points' THEN pgs.points
                   WHEN 'rebounds' THEN pgs.rebounds
                   WHEN 'assists' THEN pgs.assists
               END as actual
        FROM model_predictions mp
        JOIN games g ON g.game_id = mp.game_id
        LEFT JOIN player_game_stats pgs ON pgs.player_id = mp.player_id AND pgs.game_id = mp.game_id
        WHERE mp.player_id = :player_id
    """
    params = {"player_id": player_id}
    if stat is not None:
        query += " AND mp.stat_type = :stat"
        params["stat"] = stat
    query += " ORDER BY g.date DESC"

    with engine.begin() as conn:
        rows = conn.execute(text(query), params).fetchall()
    return [
        PredictionOut(
            game_id=r[0], date=str(r[1]), stat_type=r[2],
            predicted_mean=r[3], predicted_std=r[4], model_version=r[5], actual=r[6],
        )
        for r in rows
    ]


@app.get("/model-performance", response_model=list[ModelPerformanceOut])
def model_performance():
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT mp.stat_type,
                       AVG(ABS(mp.predicted_mean - CASE mp.stat_type
                           WHEN 'points' THEN pgs.points
                           WHEN 'rebounds' THEN pgs.rebounds
                           WHEN 'assists' THEN pgs.assists
                       END)) as mae,
                       COUNT(*) as n
                FROM model_predictions mp
                JOIN player_game_stats pgs ON pgs.player_id = mp.player_id AND pgs.game_id = mp.game_id
                GROUP BY mp.stat_type
                """
            )
        ).fetchall()
    return [ModelPerformanceOut(stat_type=r[0], mae=float(r[1]), n=r[2]) for r in rows]
