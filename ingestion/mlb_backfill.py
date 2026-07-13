"""MLB ingestion via MLB's official StatsAPI (statsapi.mlb.com).

Why not API-Sports like the NBA path: their baseball API has no player-level
box-score endpoint (verified 2026-07-13 — /games/statistics/players doesn't
exist there), and player stats are the whole point. StatsAPI is free, needs no
key, and has no meaningful quota; a polite request pace is still kept.

Writes to the same tables as ingestion/backfill.py, with sport='mlb' and every
StatsAPI numeric ID shifted by SPORTS['mlb']['id_offset'] (see config.py) so
they can't collide with NBA rows.

Players are derived from box scores rather than a roster endpoint: box scores
include traded/called-up players a current-roster pull would miss, which would
otherwise break the player_game_stats FK. A player's team_id reflects the side
they played for in the most recently processed game — same "latest pull"
simplification as the NBA path. Bench players with no batting/pitching stats
in a game are skipped entirely.

games.status: StatsAPI's codedGameState 'F' (final) is stored as 'FT' to match
the convention the API layer and Budgerr's auto-settlement already filter on;
other states are stored as the raw single-letter code.
"""

import argparse
import time

import requests

from ingestion import db
from ingestion.config import SPORTS

STATSAPI_BASE_URL = "https://statsapi.mlb.com/api/v1"
MLB_SPORT_ID = 1  # StatsAPI's own sportId for MLB
MLB_ID_OFFSET = SPORTS["mlb"]["id_offset"]

DEFAULT_SEASON = "2026"
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
SECONDS_BETWEEN_REQUESTS = 0.3


class MLBStatsClient:
    def __init__(self):
        self.session = requests.Session()
        self._last_request_at = 0.0

    def get(self, path, params=None):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < SECONDS_BETWEEN_REQUESTS:
            time.sleep(SECONDS_BETWEEN_REQUESTS - elapsed)

        url = f"{STATSAPI_BASE_URL}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            self._last_request_at = time.monotonic()
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code >= 500 and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"Exhausted retries fetching {path}")


def extract_batting_stats(batting):
    """Long-format stats from a boxscore batting dict — stat names chosen to
    line up with common MLB prop markets, plus at_bats as the exposure measure
    (the MLB analogue of minutes). Zeros are real outcomes and are kept: a
    0-hit game is exactly what an under needs.
    """
    hits = batting.get("hits", 0)
    doubles = batting.get("doubles", 0)
    triples = batting.get("triples", 0)
    home_runs = batting.get("homeRuns", 0)
    return {
        "hits": hits,
        "total_bases": hits + doubles + 2 * triples + 3 * home_runs,
        "home_runs": home_runs,
        "rbis": batting.get("rbi", 0),
        "runs": batting.get("runs", 0),
        "stolen_bases": batting.get("stolenBases", 0),
        "batter_strikeouts": batting.get("strikeOuts", 0),
        "walks": batting.get("baseOnBalls", 0),
        "at_bats": batting.get("atBats", 0),
    }


def extract_pitching_stats(pitching):
    return {
        "pitcher_strikeouts": pitching.get("strikeOuts", 0),
        "earned_runs": pitching.get("earnedRuns", 0),
        "hits_allowed": pitching.get("hits", 0),
        "walks_allowed": pitching.get("baseOnBalls", 0),
        "outs_recorded": pitching.get("outs", 0),
    }


def backfill_teams(client, engine, season):
    payload = client.get("/teams", params={"sportId": MLB_SPORT_ID, "season": season})
    teams = payload.get("teams", [])
    with engine.begin() as conn:
        for team in teams:
            db.upsert(
                conn,
                "teams",
                ["team_id"],
                {
                    "team_id": team["id"] + MLB_ID_OFFSET,
                    "sport": "mlb",
                    "name": team["name"],
                    # AL/NL slots naturally into the NBA-era conference column.
                    "conference": (team.get("league") or {}).get("name"),
                },
            )
    print(f"teams: upserted {len(teams)}")


def backfill_games(client, engine, season):
    payload = client.get(
        "/schedule",
        params={"sportId": MLB_SPORT_ID, "season": season, "gameType": "R"},
    )
    games = [g for day in payload.get("dates", []) for g in day.get("games", [])]

    finished = []
    with engine.begin() as conn:
        for game in games:
            state = (game.get("status") or {}).get("codedGameState")
            status = "FT" if state == "F" else state
            db.upsert(
                conn,
                "games",
                ["game_id"],
                {
                    "game_id": game["gamePk"] + MLB_ID_OFFSET,
                    "sport": "mlb",
                    # officialDate is the home-team local date; gameDate is UTC
                    # and rolls night games onto the next day.
                    "date": game["officialDate"],
                    "home_team_id": game["teams"]["home"]["team"]["id"] + MLB_ID_OFFSET,
                    "away_team_id": game["teams"]["away"]["team"]["id"] + MLB_ID_OFFSET,
                    "status": status,
                },
            )
            if state == "F":
                finished.append(game)

    print(f"games: upserted {len(games)} ({len(finished)} finished)")
    return finished


def _upsert_player_rows(conn, game, boxscore):
    """Players and their long-format stat rows for one finished game."""
    stat_rows = 0
    for side in ("home", "away"):
        team_id = game["teams"][side]["team"]["id"] + MLB_ID_OFFSET
        for entry in boxscore["teams"][side]["players"].values():
            batting = (entry.get("stats") or {}).get("batting") or {}
            pitching = (entry.get("stats") or {}).get("pitching") or {}

            batted = bool(batting.get("plateAppearances") or batting.get("atBats"))
            pitched = bool(pitching.get("battersFaced") or pitching.get("outs"))
            if not batted and not pitched:
                continue  # bench player, never entered the game

            person = entry["person"]
            player_id = person["id"] + MLB_ID_OFFSET
            db.upsert(
                conn,
                "players",
                ["player_id"],
                {
                    "player_id": player_id,
                    "sport": "mlb",
                    "name": person["fullName"],
                    "team_id": team_id,
                    "position": (entry.get("position") or {}).get("abbreviation"),
                },
            )

            stats = {}
            if batted:
                stats.update(extract_batting_stats(batting))
            if pitched:
                stats.update(extract_pitching_stats(pitching))
            for stat_type, value in stats.items():
                db.upsert(
                    conn,
                    "player_game_stats",
                    ["player_id", "game_id", "stat_type"],
                    {
                        "player_id": player_id,
                        "game_id": game["gamePk"] + MLB_ID_OFFSET,
                        "stat_type": stat_type,
                        "value": value,
                    },
                )
                stat_rows += 1
    return stat_rows


def backfill_team_stats(client, engine, season):
    """Per-team, per-game stats from linescores — currently first-inning runs
    (for the under-1.5 first-inning market model) and full-game runs. One
    hydrated schedule request covers the whole season.
    """
    payload = client.get(
        "/schedule",
        params={"sportId": MLB_SPORT_ID, "season": season, "gameType": "R", "hydrate": "linescore"},
    )
    games = [g for day in payload.get("dates", []) for g in day.get("games", [])]

    rows = 0
    with engine.begin() as conn:
        for game in games:
            if (game.get("status") or {}).get("codedGameState") != "F":
                continue
            innings = (game.get("linescore") or {}).get("innings") or []
            if not innings:
                continue
            game_id = game["gamePk"] + MLB_ID_OFFSET
            for side in ("home", "away"):
                team_id = game["teams"][side]["team"]["id"] + MLB_ID_OFFSET
                first = innings[0].get(side) or {}
                stats = {
                    "runs_inning_1": first.get("runs"),
                    "runs": sum((inn.get(side) or {}).get("runs") or 0 for inn in innings),
                }
                for stat_type, value in stats.items():
                    if value is None:
                        continue
                    db.upsert(
                        conn,
                        "team_game_stats",
                        ["team_id", "game_id", "stat_type"],
                        {"team_id": team_id, "game_id": game_id, "stat_type": stat_type, "value": value},
                    )
                    rows += 1
    print(f"team_game_stats: upserted {rows} rows from linescores")


def backfill_player_stats(client, engine, finished_games):
    with engine.begin() as conn:
        already_done = db.game_ids_with_stats(conn)

    remaining = [g for g in finished_games if g["gamePk"] + MLB_ID_OFFSET not in already_done]
    print(f"player_game_stats: {len(finished_games) - len(remaining)} finished games already loaded, "
          f"{len(remaining)} remaining")

    loaded = stat_rows = 0
    for game in remaining:
        boxscore = client.get(f"/game/{game['gamePk']}/boxscore")
        with engine.begin() as conn:
            stat_rows += _upsert_player_rows(conn, game, boxscore)
        loaded += 1
        if loaded % 100 == 0:
            print(f"  ...{loaded}/{len(remaining)} games loaded")

    print(f"player_game_stats: loaded box scores for {loaded} games this run ({stat_rows} stat rows)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["teams", "games", "stats", "linescores", "all"], default="all")
    parser.add_argument("--season", default=DEFAULT_SEASON, help="e.g. 2026")
    args = parser.parse_args()

    client = MLBStatsClient()
    engine = db.get_engine()

    if args.only in ("teams", "all"):
        backfill_teams(client, engine, args.season)

    finished_games = None
    if args.only in ("games", "stats", "all"):
        finished_games = backfill_games(client, engine, args.season)

    if args.only in ("stats", "all"):
        backfill_player_stats(client, engine, finished_games)

    if args.only in ("linescores", "all"):
        backfill_team_stats(client, engine, args.season)


if __name__ == "__main__":
    main()
