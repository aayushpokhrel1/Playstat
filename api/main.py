import json
import os
from datetime import date as date_type

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.schemas import (
    BacktestRunOut,
    BoxScoreOut,
    EdgeOut,
    GameLogEntry,
    ModelPerformanceOut,
    ParlayLeg,
    ParlayRecommendationOut,
    PlayerOut,
    PredictionOut,
    TeamOut,
)
from ingestion.db import get_engine

app = FastAPI(title="Playstat API")

_cors_origins = os.environ.get(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:8081"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in _cors_origins.split(",") if origin.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.get("/box-scores", response_model=list[BoxScoreOut])
def box_scores(date: date_type):
    """Final (status='FT') box scores for a given date, for external consumers
    (e.g. Budgerr's bet auto-settlement) to cross-reference against open bet legs.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT pgs.player_id, p.name, pgs.game_id, g.date, pgs.points, pgs.rebounds, pgs.assists
                FROM player_game_stats pgs
                JOIN players p ON p.player_id = pgs.player_id
                JOIN games g ON g.game_id = pgs.game_id
                WHERE g.date = :date AND g.status = 'FT'
                """
            ),
            {"date": date},
        ).fetchall()
    return [
        BoxScoreOut(
            player_id=r[0], player_name=r[1], game_id=r[2], date=str(r[3]),
            points=r[4], rebounds=r[5], assists=r[6],
        )
        for r in rows
    ]


@app.get("/edges", response_model=list[EdgeOut])
def list_edges():
    """Current positive-edge legs, for external consumers (e.g. Budgerr's bet
    quick-entry pre-fill) — empty until prop_lines has real data (~October).
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT e.player_id, p.name, p.team_id, e.game_id, g.date, e.stat_type, e.side,
                       pl.line_value,
                       CASE e.side WHEN 'over' THEN pl.over_odds ELSE pl.under_odds END AS odds,
                       e.model_prob, e.edge
                FROM edges e
                JOIN players p ON p.player_id = e.player_id
                JOIN games g ON g.game_id = e.game_id
                JOIN (
                    SELECT DISTINCT ON (player_id, game_id, stat_type)
                        player_id, game_id, stat_type, line_value, over_odds, under_odds
                    FROM prop_lines
                    ORDER BY player_id, game_id, stat_type, pulled_at DESC
                ) pl ON pl.player_id = e.player_id AND pl.game_id = e.game_id AND pl.stat_type = e.stat_type
                WHERE e.edge > 0
                ORDER BY e.edge DESC
                """
            )
        ).fetchall()
    return [
        EdgeOut(
            player_id=r[0], player_name=r[1], team_id=r[2], game_id=r[3], date=str(r[4]),
            stat_type=r[5], side=r[6], line_value=r[7], odds=r[8], model_prob=r[9], edge=r[10],
        )
        for r in rows
    ]


@app.get("/parlay-recommendations", response_model=list[ParlayRecommendationOut])
def list_parlay_recommendations(limit: int = 10):
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT parlay_id, created_at, target_payout, joint_prob, combined_odds, legs
                FROM parlay_recommendations
                ORDER BY created_at DESC, joint_prob DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).fetchall()

    results = []
    for r in rows:
        legs_raw = r[5] if isinstance(r[5], list) else json.loads(r[5])
        results.append(
            ParlayRecommendationOut(
                parlay_id=r[0],
                created_at=str(r[1]),
                target_payout=r[2],
                joint_prob=r[3],
                combined_odds=r[4],
                legs=[ParlayLeg(**leg) for leg in legs_raw],
            )
        )
    return results


@app.get("/backtest-history", response_model=list[BacktestRunOut])
def backtest_history(stat: str | None = None):
    query = "SELECT run_id, run_at, stat_type, model_version, n_test_games, mae, coverage_16, coverage_84 FROM backtest_runs"
    params = {}
    if stat is not None:
        query += " WHERE stat_type = :stat"
        params["stat"] = stat
    query += " ORDER BY run_at DESC"

    with engine.begin() as conn:
        rows = conn.execute(text(query), params).fetchall()
    return [
        BacktestRunOut(
            run_id=r[0], run_at=str(r[1]), stat_type=r[2], model_version=r[3],
            n_test_games=r[4], mae=float(r[5]) if r[5] is not None else None,
            coverage_16=float(r[6]) if r[6] is not None else None,
            coverage_84=float(r[7]) if r[7] is not None else None,
        )
        for r in rows
    ]
