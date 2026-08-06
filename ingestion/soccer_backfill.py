"""Ingest MLS/UCL (soccer) data from API-Sports FOOTBALL (v3.football.api-sports.io)
into the shared multi-sport schema. Different endpoints/shape than the basketball
backfill (ingestion/backfill.py): /fixtures + /fixtures/players, nested statistics,
goals.home/away for scores. Free tier = seasons 2022-2024 only (current is paid).
Reuses the generic APISportsClient (host comes from SPORTS[mls][base_url])."""
import argparse

from ingestion import db
from ingestion.api_client import APISportsClient, QuotaExhaustedError
from ingestion.config import SPORTS

SPORT = "mls"
SEASONS = [2022, 2023, 2024]  # free-tier accessible seasons

# API-Sports football final statuses: FT (regulation), AET (after extra time),
# PEN (penalty shootout). All are final and must settle.
SOCCER_FINAL_STATUSES = {"FT", "AET", "PEN"}


def is_soccer_final(status):
    return status in SOCCER_FINAL_STATUSES


def soccer_team_points_rows(fixture, game_id, home_team_id, away_team_id):
    """Final goals per team as team_game_stats points rows (match-total
    settlement reads home+away like MLB runs / NFL/NBA points). Empty if a goal
    count is missing/None."""
    goals = fixture.get("goals") or {}
    home, away = goals.get("home"), goals.get("away")
    if home is None or away is None:
        return []
    return [
        {"team_id": home_team_id, "game_id": game_id, "stat_type": "points", "value": int(home)},
        {"team_id": away_team_id, "game_id": game_id, "stat_type": "points", "value": int(away)},
    ]


def extract_soccer_player_stats(stat_block):
    """{stat_type: value} from a /fixtures/players statistics[0] dict. Keys match
    STAT_MAPS[mls] values so SGO props settle. None values dropped."""
    shots = stat_block.get("shots") or {}
    tackles = stat_block.get("tackles") or {}
    out = {
        "shots": shots.get("total"),
        "shots_on_goal": shots.get("on"),
        "tackles": tackles.get("total"),
    }
    return {k: v for k, v in out.items() if v is not None}


def backfill_teams_and_games(conn, fixtures, offset, sport="mls"):
    """Upsert teams (from fixture home/away) + games; write scores for finals."""
    for fx in fixtures:
        f = fx["fixture"]; teams = fx["teams"]
        for side in ("home", "away"):
            t = teams[side]
            db.upsert(conn, "teams", ["team_id"],
                      {"team_id": t["id"] + offset, "sport": sport, "name": t["name"]})
        game_id = f["id"] + offset
        status = (f.get("status") or {}).get("short")
        db.upsert(conn, "games", ["game_id"], {
            "game_id": game_id, "sport": sport, "date": f["date"][:10],
            "home_team_id": teams["home"]["id"] + offset,
            "away_team_id": teams["away"]["id"] + offset,
            "status": status,
        })
        if is_soccer_final(status):
            for pr in soccer_team_points_rows(
                fx, game_id, teams["home"]["id"] + offset, teams["away"]["id"] + offset
            ):
                db.upsert(conn, "team_game_stats", ["team_id", "game_id", "stat_type"], pr)


def backfill_fixtures(client, engine, season, sport="mls"):
    offset = SPORTS[sport]["id_offset"]
    fixtures = client.get("/fixtures", params={"league": SPORTS[sport]["league_id"], "season": season})
    with engine.begin() as conn:
        backfill_teams_and_games(conn, fixtures, offset, sport)
    finished = [fx for fx in fixtures if is_soccer_final((fx["fixture"].get("status") or {}).get("short"))]
    print(f"fixtures {season}: upserted {len(fixtures)} ({len(finished)} finished)")
    return finished


def backfill_player_stats(client, engine, finished_fixtures, sport="mls"):
    offset = SPORTS[sport]["id_offset"]
    with engine.begin() as conn:
        already = db.game_ids_with_stats(conn)
    remaining = [fx for fx in finished_fixtures if fx["fixture"]["id"] + offset not in already]
    print(f"player_stats: {len(already)} games already loaded, {len(remaining)} remaining")
    loaded = 0
    for fx in remaining:
        game_id = fx["fixture"]["id"] + offset
        teams = client.get("/fixtures/players", params={"fixture": fx["fixture"]["id"]})
        with engine.begin() as conn:
            for team_block in teams:
                team_id = team_block["team"]["id"] + offset
                for p in team_block.get("players", []):
                    pid = p["player"]["id"] + offset
                    db.upsert(conn, "players", ["player_id"], {
                        "player_id": pid, "sport": sport, "name": p["player"]["name"],
                        "team_id": team_id, "position": None,
                    })
                    stats = (p.get("statistics") or [{}])[0]
                    for stat_type, value in extract_soccer_player_stats(stats).items():
                        db.upsert(conn, "player_game_stats",
                                  ["player_id", "game_id", "stat_type"],
                                  {"player_id": pid, "game_id": game_id,
                                   "stat_type": stat_type, "value": value})
        loaded += 1
    print(f"player_stats: loaded {loaded} games this run")
    return loaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=["mls", "ucl"], default=SPORT)
    parser.add_argument("--season", default="all")
    parser.add_argument("--only", choices=["fixtures", "stats", "all"], default="all")
    args = parser.parse_args()
    seasons = SEASONS if args.season == "all" else [int(args.season)]
    client = APISportsClient(args.sport)
    engine = db.get_engine()
    try:
        for season in seasons:
            finished = backfill_fixtures(client, engine, season, args.sport)
            if args.only in ("stats", "all"):
                backfill_player_stats(client, engine, finished, args.sport)
    except QuotaExhaustedError as e:
        print(f"Stopping: {e}\nRe-run later to resume — loaded games are skipped.")


if __name__ == "__main__":
    main()
