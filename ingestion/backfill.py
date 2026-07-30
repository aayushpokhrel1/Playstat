import argparse
import os
import subprocess
import sys

from sqlalchemy import text

from ingestion import db
from ingestion.api_client import APISportsClient, QuotaExhaustedError
from ingestion.config import SPORTS

DEFAULT_SEASON = "2023-2024"
LAUNCHD_PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.playstat.backfill.plist")

from datetime import date as _date


def current_nba_season(today=None):
    """API-Sports season label (YYYY-YYYY+1) for the NBA season in progress.
    NBA runs ~Oct..Jun and is labelled by its START year. Sep..Dec -> this year
    starts the season; Jan..Aug -> the prior year did."""
    today = today or _date.today()
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}-{start + 1}"


def parse_minutes(min_str):
    """API-Sports basketball reports minutes as "MM:SS" (or just "MM"). Returns float minutes."""
    if not min_str:
        return None
    parts = min_str.split(":")
    minutes = int(parts[0])
    seconds = int(parts[1]) if len(parts) > 1 else 0
    return round(minutes + seconds / 60, 2)


def extract_nba_stats(entry):
    """Long-format stats from one API-Basketball box-score entry: {stat_type: value}."""
    return {
        "points": entry.get("points"),
        "rebounds": (entry.get("rebounds") or {}).get("total"),
        "assists": entry.get("assists"),
        "minutes": parse_minutes(entry.get("minutes")),
    }


# sport -> box-score entry parser, for sports ingested via API-Sports. Adding
# one means adding its extractor here (plus its entry in config.SPORTS) — the
# rest of this file is generic. mlb is NOT here: API-Sports baseball has no
# player box scores, so MLB lives on MLB StatsAPI in ingestion/mlb_backfill.py.
# nfl pending the same player-stats coverage check on API-Sports football.
STAT_EXTRACTORS = {
    "nba": extract_nba_stats,
}

CONFERENCE_PLACEHOLDER_NAMES = {"East", "West"}


def backfill_teams(client, engine, season, sport):
    offset = SPORTS[sport]["id_offset"]
    all_teams = client.get("/teams", params={"league": SPORTS[sport]["league_id"], "season": season})
    teams = [t for t in all_teams if t["name"] not in CONFERENCE_PLACEHOLDER_NAMES]
    with engine.begin() as conn:
        for team in teams:
            db.upsert(
                conn,
                "teams",
                ["team_id"],
                {"team_id": team["id"] + offset, "sport": sport, "name": team["name"]},
            )
    print(f"teams: upserted {len(teams)}")
    return [team["id"] + offset for team in teams]


def backfill_players(client, engine, team_ids, season, sport):
    offset = SPORTS[sport]["id_offset"]
    total = 0
    with engine.begin() as conn:
        for team_id in team_ids:
            players = client.get(
                "/players", params={"team": team_id - offset, "season": season}
            )
            for player in players:
                db.upsert(
                    conn,
                    "players",
                    ["player_id"],
                    {
                        "player_id": player["id"] + offset,
                        "sport": sport,
                        "name": player["name"],
                        "team_id": team_id,
                        "position": player.get("position"),
                    },
                )
            total += len(players)
    print(f"players: upserted {total}")


NBA_FINAL_STATUSES = {"FT", "AOT"}


def is_final(status):
    return status in NBA_FINAL_STATUSES


def nba_team_points_rows(game, game_id, home_team_id, away_team_id):
    """Pure: final-score rows for team_game_stats. Empty for an unscored game.
    Each team final points stored as a points actual so settlement reads them
    like MLB runs_inning_1 / NFL points (mirrors nfl_backfill.team_points_rows)."""
    scores = game.get("scores") or {}
    home = (scores.get("home") or {}).get("total")
    away = (scores.get("away") or {}).get("total")
    if home is None or away is None:
        return []
    return [
        {"team_id": home_team_id, "game_id": game_id, "stat_type": "points", "value": int(home)},
        {"team_id": away_team_id, "game_id": game_id, "stat_type": "points", "value": int(away)},
    ]


def backfill_games(client, engine, season, sport):
    offset = SPORTS[sport]["id_offset"]
    all_games = client.get("/games", params={"league": SPORTS[sport]["league_id"], "season": season})
    # Exclude exhibitions like the All-Star Game, played between "East"/"West", not real teams.
    games = [
        g
        for g in all_games
        if g["teams"]["home"]["name"] not in CONFERENCE_PLACEHOLDER_NAMES
        and g["teams"]["away"]["name"] not in CONFERENCE_PLACEHOLDER_NAMES
    ]
    finished = [g for g in games if is_final((g.get("status") or {}).get("short"))]

    with engine.begin() as conn:
        for game in games:
            db.upsert(
                conn,
                "games",
                ["game_id"],
                {
                    "game_id": game["id"] + offset,
                    "sport": sport,
                    "date": game["date"][:10],
                    "home_team_id": game["teams"]["home"]["id"] + offset,
                    "away_team_id": game["teams"]["away"]["id"] + offset,
                    "status": (game.get("status") or {}).get("short"),
                },
            )
            if is_final((game.get("status") or {}).get("short")):
                for pr in nba_team_points_rows(
                    game,
                    game["id"] + offset,
                    game["teams"]["home"]["id"] + offset,
                    game["teams"]["away"]["id"] + offset,
                ):
                    db.upsert(conn, "team_game_stats", ["team_id", "game_id", "stat_type"], pr)
    print(f"games: upserted {len(games)} ({len(finished)} finished)")
    return finished


def backfill_player_stats(client, engine, finished_games, sport):
    offset = SPORTS[sport]["id_offset"]
    extract_stats = STAT_EXTRACTORS[sport]

    with engine.begin() as conn:
        already_done = db.game_ids_with_stats(conn)

    remaining = [g for g in finished_games if g["id"] + offset not in already_done]
    print(f"player_game_stats: {len(already_done)} games already loaded, {len(remaining)} remaining")

    loaded = 0
    for game in remaining:
        entries = client.get(
            "/games/statistics/players", params={"id": game["id"]}
        )
        with engine.begin() as conn:
            for entry in entries:
                player_id = entry.get("player", {}).get("id")
                if player_id is None:
                    continue
                for stat_type, value in extract_stats(entry).items():
                    if value is None:
                        continue
                    db.upsert(
                        conn,
                        "player_game_stats",
                        ["player_id", "game_id", "stat_type"],
                        {
                            "player_id": player_id + offset,
                            "game_id": game["id"] + offset,
                            "stat_type": stat_type,
                            "value": value,
                        },
                    )
        loaded += 1

    print(f"player_game_stats: loaded box scores for {loaded} games this run")
    return len(remaining) - loaded  # games still missing stats after this run


def disable_scheduled_backfill():
    if not os.path.exists(LAUNCHD_PLIST_PATH):
        return
    print("All finished games have box scores — disabling the scheduled daily backfill job.")
    subprocess.run(["launchctl", "unload", LAUNCHD_PLIST_PATH], check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        choices=["teams", "players", "games", "stats", "all"],
        default="all",
    )
    parser.add_argument("--season", default=DEFAULT_SEASON, help="e.g. 2023-2024")
    parser.add_argument("--sport", choices=list(STAT_EXTRACTORS), default="nba")
    args = parser.parse_args()
    season = current_nba_season() if args.season == "current" else args.season

    client = APISportsClient(args.sport)
    engine = db.get_engine()

    try:
        team_ids = None
        finished_games = None

        if args.only in ("teams", "players", "all"):
            team_ids = backfill_teams(client, engine, season, args.sport)

        if args.only in ("players", "all"):
            if team_ids is None:
                with engine.begin() as conn:
                    team_ids = [
                        row[0]
                        for row in conn.execute(
                            text("SELECT team_id FROM teams WHERE sport = :sport"),
                            {"sport": args.sport},
                        ).fetchall()
                    ]
            backfill_players(client, engine, team_ids, season, args.sport)

        if args.only in ("games", "stats", "all"):
            finished_games = backfill_games(client, engine, season, args.sport)

        if args.only in ("stats", "all"):
            if finished_games is None:
                raise RuntimeError("--only stats requires games to already be loaded")
            still_remaining = backfill_player_stats(client, engine, finished_games, args.sport)
            if still_remaining == 0:
                disable_scheduled_backfill()

    except QuotaExhaustedError as e:
        print(f"Stopping: {e}")
        print("Re-run this script later (e.g. tomorrow) to resume — already-loaded data will be skipped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
