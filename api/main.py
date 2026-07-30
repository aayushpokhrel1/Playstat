import json
import os
from datetime import date as date_type

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.auth import require_api_key
from api.schemas import (
    BacktestRunOut,
    BetPerformanceOut,
    BoxScoreOut,
    BuilderLegOut,
    BuilderParlayOut,
    BuilderRecordDailyOut,
    BuilderRecordOut,
    BuilderSearchOut,
    ClvSummaryOut,
    EdgeDistributionOut,
    EdgeOut,
    GameLogEntry,
    GameOut,
    GamePredictionOut,
    ModelPerformanceOut,
    ParlayLeg,
    ParlayRecommendationOut,
    PlayerOut,
    PmfPoint,
    PredictionOut,
    SavedBuilderParlayOut,
    TeamOut,
)
from ingestion.db import get_engine
from modeling import settle
from modeling.distributions import pmf_list, prob_over
from modeling.train import model_version, stat_family
from optimizer import builder, builder_core

app = FastAPI(title="Playstat API", dependencies=[Depends(require_api_key)])

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


@app.get("/health")
def health():
    """Liveness/readiness probe for container healthchecks (Docker Compose /
    systemd on the deployment host). Unauthenticated by design — see
    api/auth.py's PUBLIC_PATHS: a healthcheck command shouldn't need an API
    key provisioned into it, and this returns status only, never data.

    Checks Postgres reachability too, so an up-but-dataless API (the failure
    mode that actually matters here — every endpoint reads the DB) reports
    unhealthy rather than healthy.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="database unreachable")
    return {"status": "ok", "database": "ok"}


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


@app.get("/edge-distributions", response_model=list[EdgeDistributionOut])
def edge_distributions():
    """Full predictive PMF behind every current positive edge (README §14.5) —
    lets the dashboard draw the whole distribution behind a "model prob 82%"
    figure, not just that single number. Same edge set and same `prop_lines`
    latest-pull join as /edges (mirrored exactly, including the DISTINCT ON
    subquery); additionally joins model_predictions for (predicted_mean,
    predicted_std) so modeling/distributions.py can reconstruct the law.
    Read-only, additive — does not touch /edges or its response shape.

    model_predictions' primary key includes model_version (old versions are
    never deleted), so a stat can have more than one row per (player, game,
    stat) key; we join without pinning the version in SQL and instead filter
    in Python against modeling.train.model_version(stat), mirroring exactly
    how modeling/edges.py's compute_edges matches predictions to the live
    model per stat.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT e.player_id, e.game_id, e.stat_type, e.side,
                       pl.line_value, mp.predicted_mean, mp.predicted_std, mp.model_version
                FROM edges e
                JOIN (
                    SELECT DISTINCT ON (player_id, game_id, stat_type)
                        player_id, game_id, stat_type, line_value, over_odds, under_odds
                    FROM prop_lines
                    ORDER BY player_id, game_id, stat_type, pulled_at DESC
                ) pl ON pl.player_id = e.player_id AND pl.game_id = e.game_id AND pl.stat_type = e.stat_type
                JOIN model_predictions mp
                  ON mp.player_id = e.player_id AND mp.game_id = e.game_id AND mp.stat_type = e.stat_type
                WHERE e.edge > 0
                """
            )
        ).fetchall()

    results = []
    for r in rows:
        player_id, game_id, stat_type, side, line_value, predicted_mean, predicted_std, row_model_version = r
        if row_model_version != model_version(stat_type):
            continue  # a stale/retired model_version row for this stat — not the live prediction

        family = stat_family(stat_type)
        predicted_mean = float(predicted_mean)
        predicted_std = float(predicted_std)
        line_value = float(line_value)

        prob_over_val = prob_over(predicted_mean, predicted_std, line_value, family)
        prob_under_val = 1.0 - prob_over_val

        pmf = None
        if family == "discrete":
            pmf = [PmfPoint(k=k, prob=p) for k, p in pmf_list(predicted_mean, predicted_std)]

        results.append(
            EdgeDistributionOut(
                player_id=player_id,
                game_id=game_id,
                stat_type=stat_type,
                side=side,
                family=family,
                line_value=line_value,
                predicted_mean=predicted_mean,
                prob_over=prob_over_val,
                prob_under=prob_under_val,
                pmf=pmf,
            )
        )
    return results


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


def _as_legs_list(raw):
    """Unwrap a parlay_recommendations.legs JSONB value into a plain list of
    leg dicts. Same defensive shape as modeling.settle._as_legs_list (README
    §15.10 bug #4): psycopg2 hands JSONB back already parsed, so the
    team/builder {"class", "legs": [...]} wrapper arrives as a dict, and
    json.loads(dict) raises TypeError, not a parse error. This endpoint's
    `kind` filter (below) keeps builder rows out entirely, but the dormant
    kind='team' path shares this exact wrapper shape and would hit the
    identical crash the moment it goes live — fixed defensively here too
    (README §15.10 bug #5).
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        return raw["legs"]
    return raw


def player_side(player_team_id, home_id, away_id):
    """Pure: which side of a game a player's (latest-pull) team_id matches.

    `players.team_id` is a "latest pull" (README §15.10 NBA note): a traded
    player's stored team can differ from the team they played for in THIS
    game. A stored team_id matching NEITHER side (traded mid-season, stale
    row, etc.) returns None so callers fall back to an un-emphasized
    matchup instead of guessing wrong (docs/superpowers/plans/
    2026-07-28-leg-team-names.md CAVEAT 2).
    """
    if player_team_id == home_id:
        return "home"
    if player_team_id == away_id:
        return "away"
    return None


def _resolve_leg_teams(leg, games, players):
    """Pure: resolve one builder leg's home_team/away_team/player_team_side
    from batched lookup maps (see _load_builder_team_context for how those
    maps get built — no DB access happens in this function itself, so it is
    directly unit-testable with plain dict fixtures).

    games: dict[game_id -> (home_id, away_id, home_name, away_name)]
    players: dict[player_id -> team_id]

    A leg whose game_id isn't found in `games` (unresolved/missing) leaves
    all three fields None rather than raising — this context is best-effort
    enrichment, never required for a leg to render.
    """
    game = games.get(leg.get("game_id"))
    if game is None:
        return {"home_team": None, "away_team": None, "player_team_side": None}
    home_id, away_id, home_name, away_name = game
    side = None
    player_id = leg.get("player_id")
    if player_id is not None and player_id in players:
        side = player_side(players[player_id], home_id, away_id)
    return {"home_team": home_name, "away_team": away_name, "player_team_side": side}


def _load_builder_team_context(engine, game_ids, player_ids):
    """Batched (no N+1) resolution of game/team + player-team-id context for
    a set of builder legs. Issues at most two queries — ONE games+teams join
    over all game_ids, ONE players query over all player-leg player_ids —
    never one query per leg. Query order is games THEN players (endpoint
    tests key off this order); either query is skipped entirely when its id
    set is empty. Returns (games, players) maps for _resolve_leg_teams.
    """
    games = {}
    if game_ids:
        with engine.begin() as conn:
            games = {
                row[0]: (row[1], row[2], row[3], row[4])
                for row in conn.execute(
                    text(
                        """
                        SELECT g.game_id, g.home_team_id, g.away_team_id,
                               ht.name AS home, at.name AS away
                        FROM games g
                        JOIN teams ht ON ht.team_id = g.home_team_id
                        JOIN teams at ON at.team_id = g.away_team_id
                        WHERE g.game_id = ANY(:ids)
                        """
                    ),
                    {"ids": list(game_ids)},
                ).fetchall()
            }
    players = {}
    if player_ids:
        with engine.begin() as conn:
            players = dict(
                conn.execute(
                    text("SELECT player_id, team_id FROM players WHERE player_id = ANY(:ids)"),
                    {"ids": list(player_ids)},
                ).fetchall()
            )
    return games, players


@app.get("/parlay-recommendations", response_model=list[ParlayRecommendationOut])
def list_parlay_recommendations(limit: int = 10):
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT parlay_id, created_at, target_payout, joint_prob, combined_odds, legs
                FROM parlay_recommendations
                -- External-contract surface (Budgerr, README §7.1/§15.6) —
                -- additive-only. This endpoint's response schema (ParlayLeg)
                -- models only the pre-builder player/team leg shape, and
                -- builder constructions (kind='builder') mix player+team legs
                -- in one parlay and have their own endpoint/schema
                -- (/parlay-builder, BuilderParlayOut). Do NOT remove this
                -- filter to "let builder rows through here too" — it will
                -- 500 the moment a builder row enters the LIMIT window
                -- (README §15.10 bug #5, hit live 2026-07-21).
                WHERE kind IN ('player', 'team')
                ORDER BY created_at DESC, joint_prob DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).fetchall()

    parlays = [(r, _as_legs_list(r[5])) for r in rows]

    # Legs are stored with player_id only for the (live) player kind; resolve
    # names in one query so consumers (Budgerr's Tonight view) can render
    # them directly. The dormant team-kind shape carries no player_id at all
    # — tolerate that rather than raising a KeyError.
    player_ids = {
        leg["player_id"] for _, legs_raw in parlays for leg in legs_raw if "player_id" in leg
    }
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
                legs=[ParlayLeg(**leg, player_name=names.get(leg.get("player_id"))) for leg in legs_raw],
            )
        )
    return results


@app.get("/parlay-builder", response_model=BuilderSearchOut)
def parlay_builder(
    target_payout: float | None = None,
    min_prob: float | None = None,
    tolerance: float = builder_core.DEFAULT_TOLERANCE,
    floor: float = builder_core.DEFAULT_FLOOR,
    min_legs: int = builder_core.DEFAULT_MIN_LEGS,
    max_legs: int = builder_core.DEFAULT_MAX_LEGS,
    top_n: int = 10,
    max_leg_reuse: int = 2,
):
    """Low-risk parlay constructions ranked by de-vigged MARKET probability.

    Returns an object `{constructions, truncated, nodes_searched, exhaustive}` —
    truncated/exhaustive report whether the search hit its node budget and
    returned partial results.

    Pin target_payout and/or min_prob. joint_prob is the honest probability the
    whole parlay hits. No edge or expected-value claim is made or returned.

    target_payout is a FLOOR, not the centre of a tolerance band: returns the
    safest (highest joint-prob) construction that pays AT LEAST target_payout.
    tolerance only sets the initial search width above that floor (as a
    fraction, e.g. 0.10 = 10% above the floor) — a performance knob, not a
    correctness one. If nothing qualifies within it the search widens
    automatically (1.5x, 3x, then unbounded) until it finds the cheapest
    qualifying construction, so tolerance never changes *which* result is
    returned, only how quickly it's found.

    max_leg_reuse caps how many returned constructions may reuse the same
    player (or, for team markets, the same game) — default 2, matching the
    CLI's --max-leg-reuse (docs/superpowers/specs/
    2026-07-29-builder-independence-design.md). 1 = fully disjoint.
    """
    if target_payout is None and min_prob is None:
        raise HTTPException(
            status_code=422,
            detail="pin at least one axis: target_payout and/or min_prob",
        )
    if max_legs < min_legs:
        raise HTTPException(status_code=422, detail="max_legs must be >= min_legs")

    legs = builder.load_legs(engine, floor)
    if not legs:
        return BuilderSearchOut(constructions=[], truncated=False, nodes_searched=0, exhaustive=True)
    stats: dict = {}
    results = builder_core.build(
        legs, target_payout=target_payout, tolerance=tolerance, min_prob=min_prob,
        min_legs=min_legs, max_legs=max_legs, top_n=top_n, stats=stats,
        max_uses=max_leg_reuse,
    )

    # Team-name context (docs/superpowers/plans/2026-07-28-leg-team-names.md)
    # — batched, no N+1: gather ids across every construction's legs first.
    all_legs = [leg for r in results for leg in r["legs"]]
    game_ids = {leg["game_id"] for leg in all_legs if leg.get("game_id") is not None}
    player_ids = {leg["player_id"] for leg in all_legs if leg.get("player_id") is not None}
    games, players = _load_builder_team_context(engine, game_ids, player_ids)

    constructions = [
        BuilderParlayOut(
            legs=[
                BuilderLegOut(
                    game_id=leg["game_id"], kind=leg["kind"], label=leg["label"],
                    player_id=leg["player_id"], stat_type=leg["stat_type"],
                    market=leg["market"], side=leg["side"], line=leg["line_value"],
                    odds=leg["american_odds"], market_prob=leg["market_prob"],
                    model_prob=leg["model_prob"],
                    **_resolve_leg_teams(leg, games, players),
                )
                for leg in r["legs"]
            ],
            combined_odds=r["combined_odds"], joint_prob=r["joint_prob"], n_legs=r["n_legs"],
        )
        for r in results
    ]
    truncated = bool(stats.get("truncated", False))
    return BuilderSearchOut(
        constructions=constructions, truncated=truncated,
        nodes_searched=int(stats.get("nodes", 0)), exhaustive=not truncated,
    )


# tier -> the legs blob's {"class": ...} value written by optimizer/builder.py
# save_builds(). "player" is today's only production shape (the mixed
# player+team across-game build) and stays the default so a caller passing no
# `tier` gets exactly today's behaviour, unchanged (README §15.9 item 3 /
# Budgerr contract — additive-only). "team" is the new dedicated team-only
# tier (--team-only). "all" skips the class filter entirely.
TIER_TO_CLASS = {"player": "across_game", "team": "team_tier"}


@app.get("/parlay-builder/saved", response_model=list[SavedBuilderParlayOut])
def saved_builder_parlays(limit: int = 10, tier: str = "player", sport: str = "mlb"):
    """The precomputed nightly low-risk builder parlays (kind='builder'), newest
    first. A fast list read (no live search) — this is the endpoint external
    consumers (Budgerr) should use, NOT the live /parlay-builder, which can take
    4-13s. Team legs (NRFI/F5) carry no team identity in `label`: they are
    game-level markets, so resolve the matchup via each leg's game_id -> /games.
    Ranked on de-vigged market probability; model_prob is context only.

    tier selects which builder class to return: "player" (default, unchanged
    behaviour — the mixed player+team across-game tier), "team" (the
    dedicated team-only tier, higher-variance NRFI/F5-only constructions,
    may be empty on any given slate), or "all" (no class filter).

    sport is an ADDITIVE filter (default "mlb", NFL builder sub-project #2):
    existing MLB rows predate the "sport" key in the legs blob, so
    COALESCE(legs->>'sport', 'mlb') reads a legacy no-key row as "mlb". Budgerr
    and the MLB dashboard, passing no sport, keep getting exactly MLB rows —
    unchanged. The NFL dashboard (#4) will pass ?sport=nfl.
    """
    if tier != "all" and tier not in TIER_TO_CLASS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown tier {tier!r}: expected one of "
                   f"{sorted(TIER_TO_CLASS) + ['all']}",
        )
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT parlay_id, created_at, target_payout, joint_prob, combined_odds, legs
                FROM parlay_recommendations
                WHERE kind = 'builder'
                AND COALESCE(legs->>'sport', 'mlb') = :sport
                """
                + ("" if tier == "all" else "AND legs->>'class' = :cls ")
                + """
                ORDER BY created_at DESC, joint_prob DESC
                LIMIT :limit
                """
            ),
            {"limit": limit, "cls": TIER_TO_CLASS.get(tier), "sport": sport},
        ).fetchall()

    parlays = [(r, _as_legs_list(r[5])) for r in rows]

    # Team-name context (docs/superpowers/plans/2026-07-28-leg-team-names.md)
    # — batched, no N+1: gather ids across every parlay's legs first. Query
    # order is main rows (above) THEN games THEN players — see
    # _load_builder_team_context / tests/test_leg_team_names.py.
    game_ids = {
        leg["game_id"] for _, legs_raw in parlays for leg in legs_raw
        if leg.get("game_id") is not None
    }
    player_ids = {
        leg["player_id"] for _, legs_raw in parlays for leg in legs_raw
        if leg.get("player_id") is not None
    }
    games, players = _load_builder_team_context(engine, game_ids, player_ids)

    out = []
    for r, legs_raw in parlays:
        out.append(
            SavedBuilderParlayOut(
                parlay_id=r[0], created_at=str(r[1]), target_payout=float(r[2]),
                joint_prob=float(r[3]), combined_odds=float(r[4]), n_legs=len(legs_raw),
                legs=[
                    BuilderLegOut(
                        game_id=leg["game_id"], kind=leg["kind"], label=leg["label"],
                        player_id=leg.get("player_id"), stat_type=leg.get("stat_type"),
                        market=leg.get("market"), side=leg["side"], line=leg["line"],
                        odds=leg["odds"], market_prob=leg["market_prob"],
                        model_prob=leg.get("model_prob"),
                        **_resolve_leg_teams(leg, games, players),
                    )
                    for leg in legs_raw
                ],
            )
        )
    return out


# reverse of TIER_TO_CLASS: legs->>'class' value -> reporting tier label
_CLASS_TO_TIER = {"across_game": "player", "team_tier": "team"}

# tier sort key: player=0, team=1, unknown classes sort after both.
_TIER_SORT_ORDER = {"player": 0, "team": 1}


def _shape_builder_record(rows):
    """Pure: rows are (cls, target_payout, n, wins, losses, pushes, pnl) as
    produced by the GROUP BY in builder_record() below. Maps cls->tier via
    _CLASS_TO_TIER, computes roi=pnl/n (0.0 when n==0), casts Decimal
    target_payout/pnl to float, and orders player-before-team then ascending
    target_payout. DB-free and unit-testable without a database.
    """
    shaped = []
    for cls, target_payout, n, wins, losses, pushes, pnl in rows:
        tier = _CLASS_TO_TIER.get(cls, cls)
        n = int(n)
        pnl = float(pnl or 0)
        shaped.append(
            BuilderRecordOut(
                tier=tier, target_payout=float(target_payout),
                n=n, wins=int(wins), losses=int(losses), pushes=int(pushes),
                pnl=pnl, roi=(pnl / n if n else 0.0),
            )
        )
    shaped.sort(key=lambda r: (_TIER_SORT_ORDER.get(r.tier, 2), r.target_payout))
    return shaped


@app.get("/parlay-builder/record", response_model=list[BuilderRecordOut])
def builder_record(sport: str = "mlb"):
    """Paper-trading builder record split by tier + target payout (README §15).
    Dashboard-only; /bet-performance is unchanged and still feeds web/app/clv.
    sport is additive (default "mlb", mirrors /parlay-builder/saved's COALESCE
    default) so NFL and MLB records don't pool (NFL builder chain #4a)."""
    with engine.begin() as conn:
        rows = conn.execute(text(
            """
            SELECT pr.legs->>'class' AS cls, pr.target_payout,
                   count(*) AS n,
                   sum((ro.result='win')::int)  AS wins,
                   sum((ro.result='loss')::int) AS losses,
                   sum((ro.result='push')::int) AS pushes,
                   sum(ro.pnl) AS pnl
            FROM recommendation_outcomes ro
            JOIN parlay_recommendations pr ON pr.parlay_id = ro.parlay_id
            WHERE pr.kind = 'builder'
              AND COALESCE(pr.legs->>'sport', 'mlb') = :sport
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ), {"sport": sport}).fetchall()
    return _shape_builder_record(rows)


def _shape_builder_record_daily(rows):
    """Pure: rows are (slate_date, n, wins, losses, pushes, pnl) as produced
    by the GROUP BY date(pr.created_at) in builder_record_daily() below.
    Computes roi=pnl/n (0.0 when n==0), casts Decimal pnl to float, and
    stringifies the date. Rows already arrive newest-first from the SQL
    ORDER BY, and that order is preserved here. DB-free and unit-testable
    without a database.
    """
    shaped = []
    for slate_date, n, wins, losses, pushes, pnl in rows:
        n = int(n)
        pnl = float(pnl or 0)
        shaped.append(
            BuilderRecordDailyOut(
                date=str(slate_date), n=n, wins=int(wins), losses=int(losses),
                pushes=int(pushes), pnl=pnl, roi=(pnl / n if n else 0.0),
            )
        )
    return shaped


@app.get("/parlay-builder/record/daily", response_model=list[BuilderRecordDailyOut])
def builder_record_daily(sport: str = "mlb"):
    """Per-day drill-down of the builder record (README §15 follow-on):
    same settled-builder data as /parlay-builder/record, grouped by slate
    date instead of tier/target_payout. Newest date first. Dashboard-only;
    /bet-performance is unchanged and still feeds web/app/clv. sport is
    additive (default "mlb") so NFL and MLB records don't pool (NFL builder
    chain #4a)."""
    with engine.begin() as conn:
        rows = conn.execute(text(
            """
            SELECT date(pr.created_at) AS slate_date, count(*) n,
                   sum((ro.result='win')::int) wins, sum((ro.result='loss')::int) losses,
                   sum((ro.result='push')::int) pushes, sum(ro.pnl) pnl
            FROM recommendation_outcomes ro JOIN parlay_recommendations pr ON pr.parlay_id=ro.parlay_id
            WHERE pr.kind='builder'
              AND COALESCE(pr.legs->>'sport', 'mlb') = :sport
            GROUP BY 1 ORDER BY 1 DESC
            """
        ), {"sport": sport}).fetchall()
    return _shape_builder_record_daily(rows)


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


@app.get("/bet-performance", response_model=list[BetPerformanceOut])
def bet_performance():
    """Paper-trading ledger aggregate (README §14.1, modeling/settle.py) — the
    honest record of whether recommended parlays/edges would have won.

    Parlay rows are broken out by their source parlay_recommendations.kind so
    the builder's paper record (README §15) doesn't pool with the legacy
    model-ranked parlays: 'parlay_model' (kind='player'), 'parlay_team'
    (kind='team'), 'parlay_builder' (kind='builder'), plus 'edge' and a
    combined 'all' row. The recommendation_outcomes.bet_type column itself
    stays 'parlay' for every parlay row — the DB CHECK constraint requires
    it — the split happens here via a LEFT JOIN onto
    parlay_recommendations.kind, not in the schema.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ro.bet_type, pr.kind,
                       COUNT(*) AS n,
                       SUM((ro.result = 'win')::int) AS wins,
                       SUM((ro.result = 'loss')::int) AS losses,
                       SUM((ro.result = 'push')::int) AS pushes,
                       SUM(ro.stake) AS total_staked,
                       SUM(ro.pnl) AS total_pnl
                FROM recommendation_outcomes ro
                LEFT JOIN parlay_recommendations pr ON pr.parlay_id = ro.parlay_id
                GROUP BY ro.bet_type, pr.kind
                """
            )
        ).fetchall()

    return [
        BetPerformanceOut(
            bet_type=label, n=n, wins=wins, losses=losses, pushes=pushes,
            total_staked=staked, total_pnl=pnl,
            roi=(pnl / staked if staked else 0.0),
        )
        for label, n, wins, losses, pushes, staked, pnl in settle.aggregate_bet_performance(rows)
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
