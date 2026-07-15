import os

from dotenv import load_dotenv

load_dotenv()

# One API-Sports account key works across their per-sport APIs (quotas are
# per-API). Env var keeps its historical name so existing .env files still work.
API_BASKETBALL_KEY = os.environ["API_BASKETBALL_KEY"]
API_BASKETBALL_BASE_URL = "https://v1.basketball.api-sports.io"

ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
ODDS_API_BASE_URL = "https://api.sportsgameodds.com/v2"

DATABASE_URL = os.environ["DATABASE_URL"]

# Per-sport config. id_offset namespaces each sport's provider numeric IDs
# into our shared teams/players/games PK space, since providers have their own
# overlapping ID ranges — nba is +0 so all pre-multi-sport rows are unchanged.
#
# Providers differ per sport: nba uses API-Sports (base_url/league_id consumed
# by ingestion/backfill.py); mlb uses MLB's official StatsAPI instead, because
# API-Sports' baseball API has no player box scores (verified 2026-07-13) —
# see ingestion/mlb_backfill.py. nfl runs on nflverse-data's static, free,
# key-less GitHub release CSVs (schedules + player_stats) — see
# ingestion/nfl_backfill.py, which only needs this entry's id_offset. The
# base_url/league_id fields below are kept only as an unverified API-Sports
# fallback reference (100 req/day quota); nfl_backfill.py does not use them.
SPORTS = {
    "nba": {
        "base_url": "https://v1.basketball.api-sports.io",
        "league_id": 12,
        "odds_league_id": "NBA",
        "id_offset": 0,
    },
    "mlb": {
        "odds_league_id": "MLB",
        "id_offset": 100_000_000,
    },
    "nfl": {
        "base_url": "https://v1.american-football.api-sports.io",
        "league_id": 1,
        "odds_league_id": "NFL",
        "id_offset": 200_000_000,
    },
}

NBA_LEAGUE_ID = SPORTS["nba"]["league_id"]
ODDS_NBA_LEAGUE_ID = SPORTS["nba"]["odds_league_id"]
