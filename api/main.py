import json
import os
from datetime import date as date_type

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.schemas import (
    BacktestRunOut,
    BoxScoreOut,
    ClvSummaryOut,
    EdgeOut,
    GameLogEntry,
    GameOut,
    GamePredictionOut,
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
        rows = conn.execute(text("SELECT team_id, name, sport FROM teams ORDER BY name")).fetchall()
    return [TeamOut(team_id=r[0], name=r[1], sport=r[2]) for r in rows]


@app.get("/players", response_model=list[PlayerOut])
def list_players(team_id: int | None = None):
    query = "SELECT player_id, name, team_id, position, sport FROM players"
    params = {}
    if team_id is not None:
        query += " WHERE team_id = :team_id"
        params["team_id"] = team_id
    query += " ORDER BY name"

    with engine.begin() as conn:
        rows = conn.execute(text(query), params).fetchall()
    return [PlayerOut(player_id=r[0], name=r[1], team_id=r[2], position=r[3], sport=r[4]) for r in rows]


@app.get("/players/{player_id}", response_model=PlayerOut)
def get_player(player_id: int):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT player_id, name, team_id, position, sport FROM players WHERE player_id = :player_id"),
            {"player_id": player_id},
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return PlayerOut(player_id=row[0], name=row[1], team_id=row[2], position=row[3], sport=row[4])


@app.get("/players/{player_id}/stats", response_model=list[GameLogEntry])
def player_stats(player_id: int, limit: int = 20):
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT pgs.game_id, g.date,
                       (MAX(pgs.value) FILTER (WHERE pgs.stat_type = 'points'))::int   AS points,
                       (MAX(pgs.value) FILTER (WHERE pgs.stat_type = 'rebounds'))::int AS rebounds,
                       (MAX(pgs.value) FILTER (WHERE pgs.stat_type = 'assists'))::int  AS assists,
                       (MAX(pgs.value) FILTER (WHERE pgs.stat_type = 'minutes'))::float AS minutes
                FROM player_game_stats pgs
                JOIN games g ON g.game_id = pgs.game_id
                WHERE pgs.player_id = :player_id
                GROUP BY pgs.game_id, g.date
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
               pgs.value::int as actual
        FROM model_predictions mp
        JOIN games g ON g.game_id = mp.game_id
        LEFT JOIN player_game_stats pgs
          ON pgs.player_id = mp.player_id AND pgs.game_id = mp.game_id AND pgs.stat_type = mp.stat_type
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
                       AVG(ABS(mp.predicted_mean - pgs.value)) as mae,
                       COUNT(*) as n
                FROM model_predictions mp
                JOIN player_game_stats pgs
                  ON pgs.player_id = mp.player_id AND pgs.game_id = mp.game_id AND pgs.stat_type = mp.stat_type
                GROUP BY mp.stat_type
                """
            )
        ).fetchall()
    return [ModelPerformanceOut(stat_type=r[0], mae=float(r[1]), n=r[2]) for r in rows]


@app.get("/games", response_model=list[GameOut])
def list_games(date: date_type, sport: str | None = None):
    """Tonight's (or any date's) slate — full schedule, not just legs with a
    positive edge, so external consumers (e.g. Budgerr's slate+budget glance
    view) can show every matchup even before prop_lines/edges have data.
    Omitting sport returns every sport's games that date (in-season sports
    rotate through the year; the consumer shouldn't have to know which is on).
    """
    query = """
        SELECT g.game_id, g.sport, g.date,
               ht.team_id, ht.name, at.team_id, at.name, g.status
        FROM games g
        JOIN teams ht ON ht.team_id = g.home_team_id
        JOIN teams at ON at.team_id = g.away_team_id
        WHERE g.date = :date
    """
    params = {"date": date}
    if sport is not None:
        query += " AND g.sport = :sport"
        params["sport"] = sport
    query += " ORDER BY g.game_id"
    with engine.begin() as conn:
        rows = conn.execute(text(query), params).fetchall()
    return [
        GameOut(
            game_id=r[0], sport=r[1], date=str(r[2]),
            home_team_id=r[3], home_team_name=r[4],
            away_team_id=r[5], away_team_name=r[6],
            status=r[7],
        )
        for r in rows
    ]


@app.get("/box-scores", response_model=list[BoxScoreOut])
def box_scores(date: date_type, sport: str | None = None):
    """Final (status='FT') box scores for a given date, for external consumers
    (e.g. Budgerr's bet auto-settlement) to cross-reference against open bet legs.

    `stats` carries every stat_type for the player's sport; the top-level
    points/rebounds/assists fields are the NBA-era contract, kept for
    backward compatibility (null for non-NBA players).
    """
    query = """
        SELECT pgs.player_id, p.name, p.sport, pgs.game_id, g.date,
               jsonb_object_agg(pgs.stat_type, pgs.value) AS stats
        FROM player_game_stats pgs
        JOIN players p ON p.player_id = pgs.player_id
        JOIN games g ON g.game_id = pgs.game_id
        WHERE g.date = :date AND g.status = 'FT'
    """
    params = {"date": date}
    if sport is not None:
        query += " AND g.sport = :sport"
        params["sport"] = sport
    query += " GROUP BY pgs.player_id, p.name, p.sport, pgs.game_id, g.date"

    with engine.begin() as conn:
        rows = conn.execute(text(query), params).fetchall()

    results = []
    for r in rows:
        stats = {k: float(v) for k, v in (r[5] or {}).items() if v is not None}
        def legacy(stat):
            return int(stats[stat]) if stat in stats else None
        results.append(
            BoxScoreOut(
                player_id=r[0], player_name=r[1], sport=r[2], game_id=r[3], date=str(r[4]),
                points=legacy("points"), rebounds=legacy("rebounds"), assists=legacy("assists"),
                stats=stats,
            )
        )
    return results


@app.get("/edges", response_model=list[EdgeOut])
def list_edges():
    """Current positive-edge legs, for external consumers (e.g. Budgerr's bet
    quick-entry pre-fill) — empty until a sport has both live prop_lines and
    model predictions (MLB lines are flowing; MLB modeling is the missing half).
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


@app.get("/game-predictions", response_model=list[GamePredictionOut])
def game_predictions(date: date_type | None = None, sport: str | None = None):
    """Game-level model outputs (e.g. first-inning total runs vs the 1.5 line),
    with the latest ingested book line for the same market when one exists.
    """
    query = """
        SELECT gp.game_id, g.date, g.sport, ht.name, at.name, gp.market, gp.line_value,
               gp.predicted_mean, gp.prob_under, gp.prob_over, gp.model_version,
               gl.line_value, gl.over_odds, gl.under_odds
        FROM game_predictions gp
        JOIN games g ON g.game_id = gp.game_id
        JOIN teams ht ON ht.team_id = g.home_team_id
        JOIN teams at ON at.team_id = g.away_team_id
        LEFT JOIN LATERAL (
            SELECT line_value, over_odds, under_odds
            FROM game_lines
            WHERE game_id = gp.game_id AND market = gp.market
            ORDER BY pulled_at DESC
            LIMIT 1
        ) gl ON true
        WHERE true
    """
    params = {}
    if date is not None:
        query += " AND g.date = :date"
        params["date"] = date
    if sport is not None:
        query += " AND g.sport = :sport"
        params["sport"] = sport
    query += " ORDER BY g.date, gp.game_id"

    with engine.begin() as conn:
        rows = conn.execute(text(query), params).fetchall()
    return [
        GamePredictionOut(
            game_id=r[0], date=str(r[1]), sport=r[2], home_team=r[3], away_team=r[4],
            market=r[5], line_value=r[6], predicted_mean=r[7], prob_under=r[8],
            prob_over=r[9], model_version=r[10],
            book_line_value=r[11], book_over_odds=r[12], book_under_odds=r[13],
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

    parlays = [(r, r[5] if isinstance(r[5], list) else json.loads(r[5])) for r in rows]

    # Legs are stored with player_id only; resolve names in one query so
    # consumers (Budgerr's Tonight view) can render them directly.
    player_ids = {leg["player_id"] for _, legs_raw in parlays for leg in legs_raw}
    names = {}
    if player_ids:
        with engine.begin() as conn:
            names = dict(
                conn.execute(
                    text("SELECT player_id, name FROM players WHERE player_id = ANY(:ids)"),
                    {"ids": list(player_ids)},
                ).fetchall()
            )

    results = []
    for r, legs_raw in parlays:
        results.append(
            ParlayRecommendationOut(
                parlay_id=r[0],
                created_at=str(r[1]),
                target_payout=r[2],
                joint_prob=r[3],
                combined_odds=r[4],
                legs=[ParlayLeg(**leg, player_name=names.get(leg["player_id"])) for leg in legs_raw],
            )
        )
    return results


@app.get("/clv-summary", response_model=list[ClvSummaryOut])
def clv_summary():
    """Closing-line value by stat type (multi-snapshot edges only) — the
    leading indicator of whether flagged edges are real. Positive avg CLV
    means the market keeps moving toward our positions after we flag them.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT stat_type, COUNT(*) AS n, AVG(clv) AS avg_clv,
                       AVG((clv > 0)::int) AS pct_positive
                FROM clv_records
                WHERE n_snapshots > 1
                GROUP BY stat_type
                ORDER BY stat_type
                """
            )
        ).fetchall()
    return [
        ClvSummaryOut(stat_type=r[0], n=r[1], avg_clv=float(r[2]), pct_positive=float(r[3]))
        for r in rows
    ]


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
