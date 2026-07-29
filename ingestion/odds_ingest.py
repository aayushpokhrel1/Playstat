import argparse

from sqlalchemy import text

from ingestion import db, matching
from ingestion.config import SPORTS
from ingestion.odds_client import SportsGameOddsClient

# SportsGameOdds statID -> our player_game_stats stat_type, per sport. Only
# markets we also ingest actuals for (so edges can be modeled and bets can be
# settled) are mapped; anything else in the odds feed is skipped.
#
# MLB IDs verified against the live feed 2026-07-13, including "points" —
# SGO normalizes each sport's primary scoring stat to "points", and MLB market
# names confirm it's runs ("<player> Runs Over/Under"). Deliberately unmapped:
# batting_singles/doubles/triples (we don't store those splits),
# batting_hits+runs+rbi (combo market), and fantasyScore.
STAT_MAPS = {
    "nba": {
        "points": "points",
        "rebounds": "rebounds",
        "assists": "assists",
    },
    "mlb": {
        "points": "runs",
        "batting_hits": "hits",
        "batting_totalBases": "total_bases",
        "batting_homeRuns": "home_runs",
        "batting_RBI": "rbis",
        "batting_strikeouts": "batter_strikeouts",
        "batting_basesOnBalls": "walks",
        "batting_stolenBases": "stolen_bases",
        "pitching_strikeouts": "pitcher_strikeouts",
        "pitching_earnedRuns": "earned_runs",
        "pitching_hits": "hits_allowed",
        "pitching_basesOnBalls": "walks_allowed",
        "pitching_outs": "outs_recorded",
    },
    # NFL player props. statID keys are PROVISIONAL (SGO NFL feed never hit;
    # docs ambiguous camel vs snake, MLB map here uses snake) -- confirm via
    # `odds_ingest --sport nfl --dry-run` against the live feed at preseason.
    # Values MUST match player_game_stats stat_types (so props are settleable).
    "nfl": {
        "passing_yards": "passing_yards",
        "rushing_yards": "rushing_yards",
        "receiving_yards": "receiving_yards",
        "receptions": "receptions",
        "targets": "targets",
        "passing_touchdowns": "passing_tds",
        "rushing_touchdowns": "rushing_tds",
        "receiving_touchdowns": "receiving_tds",
        "passing_completions": "completions",
        "rushing_attempts": "carries",
        "passing_attempts": "pass_attempts",
        "interceptions": "interceptions_thrown",
    },
}


# Game-level (not player) markets to ingest into game_lines, per sport:
# market name -> (statID, statEntityID, periodID). MLB "1st Inning Over/Under"
# is statID points / entity all / period 1i in the SGO feed (verified live).
# F5 ("first 5 innings" total runs) is period 1ix5 — SGO's "innings 1 through 5"
# cumulative family (1ix3/1ix5/1ix7); verified live 2026-07-17 as fully priced on
# our free tier (line ~3.5, over/under quoted across fanduel/bovada/betmgm/draftkings).
# Unlike NRFI's fixed 0.5 line, F5 lines vary per game — the model derives P(under
# actual_line) from a predicted mean rather than a single fixed threshold.
GAME_MARKETS = {
    "mlb": {
        "first_inning_runs": ("points", "all", "1i"),
        "f5_runs": ("points", "all", "1ix5"),
    },
    # NFL full-game total (points over/under). Spread/moneyline are home/away
    # markets that don't fit game_lines' over/under columns -- deferred to
    # sub-project #3 (schema change + settlement). periodID "game" / statID
    # "points" / entity "all" confirmed from the MLB game-market pattern.
    "nfl": {
        "game_total": ("points", "all", "game"),
    },
}


def parse_american_odds(odds_str):
    if odds_str is None:
        return None
    return int(odds_str)


def collect_prop_rows(event, stat_map):
    """Group each event's raw over/under odd pairs into one row per (player, stat)."""
    odds = event.get("odds", {})
    event_players = event.get("players", {})
    rows = {}

    for odd in odds.values():
        stat_type = stat_map.get(odd.get("statID"))
        if stat_type is None:
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

        key = (entity_id, stat_type)
        row = rows.setdefault(
            key,
            {"player_name": player_info["name"], "stat_type": stat_type},
        )
        row["line_value"] = odd.get("bookOverUnder")
        row[f"{side}_odds"] = parse_american_odds(odd.get("bookOdds"))

    return list(rows.values())


def collect_game_rows(event, game_markets):
    """Game-level over/under lines (e.g. first-inning total runs): one row per
    market with whatever line the book quotes (0.5 NRFI-style is common)."""
    rows = {}
    for odd in event.get("odds", {}).values():
        for market, (stat_id, entity_id, period_id) in game_markets.items():
            if (odd.get("statID"), odd.get("statEntityID"), odd.get("periodID")) != (stat_id, entity_id, period_id):
                continue
            if odd.get("betTypeID") != "ou":
                continue
            side = odd.get("sideID")
            if side not in ("over", "under"):
                continue
            row = rows.setdefault(market, {"market": market})
            row["line_value"] = odd.get("bookOverUnder")
            row[f"{side}_odds"] = parse_american_odds(odd.get("bookOdds"))
    return list(rows.values())


def observed_statid_summary(events, stat_map, game_markets):
    """Pure: count each over/under odd across events by whether its statID is
    known (in stat_map, or a configured game-market statID) or unmapped. The
    reporting core of --dry-run, so the map can be confirmed against the live
    feed without writing the DB."""
    game_stat_ids = {stat_id for (stat_id, _entity, _period) in game_markets.values()}
    mapped, unmapped = {}, {}
    for event in events:
        for odd in event.get("odds", {}).values():
            if odd.get("betTypeID") != "ou":
                continue
            stat_id = odd.get("statID")
            bucket = mapped if (stat_id in stat_map or stat_id in game_stat_ids) else unmapped
            bucket[stat_id] = bucket.get(stat_id, 0) + 1
    return {"mapped": mapped, "unmapped": unmapped}


def ingest_odds(sport="nba", dry_run=False):
    stat_map = STAT_MAPS[sport]
    game_markets = GAME_MARKETS.get(sport, {})
    odds_league_id = SPORTS[sport]["odds_league_id"]

    client = SportsGameOddsClient()
    engine = db.get_engine()

    with engine.begin() as conn:
        team_index = matching.load_team_index(conn, sport)
        player_index = matching.load_player_index(conn, sport)
        game_index = matching.load_game_index(conn, sport)

    events = list(client.get_events(odds_league_id, odds_available=True))

    if dry_run:
        summary = observed_statid_summary(events, stat_map, game_markets)
        print(f"({sport}) DRY RUN — events: {len(events)}")
        print(f"  mapped statIDs:   {summary['mapped']}")
        print(f"  UNMAPPED statIDs: {summary['unmapped']}")
        matched = unmatched_games = 0
        for event in events:
            home = event.get("teams", {}).get("home", {}).get("names", {}).get("long")
            away = event.get("teams", {}).get("away", {}).get("names", {}).get("long")
            date = matching.utc_start_to_local_date(event.get("status", {}).get("startsAt"))
            hid, aid = matching.match_team(home, team_index), matching.match_team(away, team_index)
            gid = matching.match_game(hid, aid, date, game_index) if (hid and aid and date) else None
            matched += gid is not None
            unmatched_games += gid is None
        print(f"  games matched: {matched}, unmatched: {unmatched_games}")
        return

    events_seen = 0
    rows_inserted = 0
    games_unmatched = 0
    players_unmatched = 0

    for event in events:
        events_seen += 1

        home_name = event.get("teams", {}).get("home", {}).get("names", {}).get("long")
        away_name = event.get("teams", {}).get("away", {}).get("names", {}).get("long")
        event_date = matching.utc_start_to_local_date(
            event.get("status", {}).get("startsAt")
        )

        home_team_id = matching.match_team(home_name, team_index)
        away_team_id = matching.match_team(away_name, team_index)
        game_id = None
        if home_team_id and away_team_id and event_date:
            game_id = matching.match_game(home_team_id, away_team_id, event_date, game_index)

        if game_id is None:
            games_unmatched += 1
            continue

        prop_rows = collect_prop_rows(event, stat_map)
        game_rows = collect_game_rows(event, game_markets)

        with engine.begin() as conn:
            for row in game_rows:
                conn.execute(
                    text(
                        "INSERT INTO game_lines (game_id, market, line_value, over_odds, under_odds) "
                        "VALUES (:game_id, :market, :line_value, :over_odds, :under_odds)"
                    ),
                    {
                        "game_id": game_id,
                        "market": row["market"],
                        "line_value": row.get("line_value"),
                        "over_odds": row.get("over_odds"),
                        "under_odds": row.get("under_odds"),
                    },
                )
                rows_inserted += 1
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
        f"({sport}) events seen: {events_seen}, prop_lines inserted: {rows_inserted}, "
        f"events with unmatched game: {games_unmatched}, unmatched players: {players_unmatched}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=list(STAT_MAPS), default="nba")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and report statID coverage + match rates without writing")
    args = parser.parse_args()
    ingest_odds(args.sport, dry_run=args.dry_run)
