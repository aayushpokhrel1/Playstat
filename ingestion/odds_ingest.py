from sqlalchemy import text

from ingestion import db, matching
from ingestion.config import ODDS_NBA_LEAGUE_ID
from ingestion.odds_client import SportsGameOddsClient

TARGET_STAT_IDS = {"points", "rebounds", "assists"}


def parse_american_odds(odds_str):
    if odds_str is None:
        return None
    return int(odds_str)


def collect_prop_rows(event):
    """Group each event's raw over/under odd pairs into one row per (player, stat)."""
    odds = event.get("odds", {})
    event_players = event.get("players", {})
    rows = {}

    for odd in odds.values():
        if odd.get("statID") not in TARGET_STAT_IDS:
            continue
        if odd.get("periodID") != "game":
            continue
        if odd.get("betTypeID") != "ou":
            continue

        entity_id = odd.get("statEntityID")
        side = odd.get("sideID")
        player_info = event_players.get(entity_id)
        if not player_info or side not in ("over", "under"):
            continue

        key = (entity_id, odd["statID"])
        row = rows.setdefault(
            key,
            {"player_name": player_info["name"], "stat_type": odd["statID"]},
        )
        row["line_value"] = odd.get("bookOverUnder")
        row[f"{side}_odds"] = parse_american_odds(odd.get("bookOdds"))

    return list(rows.values())


def ingest_odds():
    client = SportsGameOddsClient()
    engine = db.get_engine()

    with engine.begin() as conn:
        team_index = matching.load_team_index(conn)
        player_index = matching.load_player_index(conn)
        game_index = matching.load_game_index(conn)

    events_seen = 0
    rows_inserted = 0
    games_unmatched = 0
    players_unmatched = 0

    for event in client.get_events(ODDS_NBA_LEAGUE_ID, odds_available=True):
        events_seen += 1

        home_name = event.get("teams", {}).get("home", {}).get("names", {}).get("long")
        away_name = event.get("teams", {}).get("away", {}).get("names", {}).get("long")
        event_date = (event.get("status", {}).get("startsAt") or "")[:10]

        home_team_id = matching.match_team(home_name, team_index)
        away_team_id = matching.match_team(away_name, team_index)
        game_id = None
        if home_team_id and away_team_id and event_date:
            game_id = matching.match_game(home_team_id, away_team_id, event_date, game_index)

        if game_id is None:
            games_unmatched += 1
            continue

        prop_rows = collect_prop_rows(event)

        with engine.begin() as conn:
            for row in prop_rows:
                player_id = matching.match_player(row["player_name"], player_index)
                if player_id is None:
                    players_unmatched += 1
                    continue
                conn.execute(
                    text(
                        "INSERT INTO prop_lines "
                        "(player_id, game_id, stat_type, line_value, over_odds, under_odds) "
                        "VALUES (:player_id, :game_id, :stat_type, :line_value, :over_odds, :under_odds)"
                    ),
                    {
                        "player_id": player_id,
                        "game_id": game_id,
                        "stat_type": row["stat_type"],
                        "line_value": row.get("line_value"),
                        "over_odds": row.get("over_odds"),
                        "under_odds": row.get("under_odds"),
                    },
                )
                rows_inserted += 1

    print(
        f"events seen: {events_seen}, prop_lines inserted: {rows_inserted}, "
        f"events with unmatched game: {games_unmatched}, unmatched players: {players_unmatched}"
    )


if __name__ == "__main__":
    ingest_odds()
