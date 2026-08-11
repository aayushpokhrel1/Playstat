import os

from dotenv import load_dotenv

load_dotenv()

# One API-Sports account key works across their per-sport APIs (quotas are
# per-API). Env var keeps its historical name so existing .env files still work.
API_BASKETBALL_KEY = os.environ["API_BASKETBALL_KEY"]
API_BASKETBALL_BASE_URL = "https://v1.basketball.api-sports.io"

ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
ODDS_API_BASE_URL = "https://api.sportsgameodds.com/v2"

# The Odds API (the-odds-api.com) — sharp-reference snapshots (README 15.9 item 14e); a DIFFERENT service from SportsGameOdds above.
THEODDS_API_KEY = os.environ.get("THE_ODDS_API_KEY")
THEODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"

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
    # MLS (soccer) — API-Sports FOOTBALL API (different host/endpoints than
    # basketball; same key). League 253. Free tier: seasons 2022-2024 only
    # (current season is paid). Ingested by ingestion/soccer_backfill.py.
    "mls": {
        "base_url": "https://v3.football.api-sports.io",
        "league_id": 253,
        "odds_league_id": "MLS",
        "id_offset": 300_000_000,
    },
    # UEFA Champions League (soccer) — SAME API-Sports FOOTBALL host/key as MLS,
    # league 2 (LIVE-VERIFIED 2026-08-05: 279 fixtures/2024, FT/AET/PEN). Free tier:
    # seasons 2022-2024 only (current is paid). Ingested by soccer_backfill --sport ucl.
    # id_offset is +500M, NOT +400M: NFL's game_id is 200M + season*100000 + ...
    # (nfl_backfill), so NFL PHYSICALLY sits at ~402M+ (2023) and climbs +0.1M/season,
    # squatting in the 400M band despite its +200M label. +400M UCL would collide with
    # NFL once raw fixture ids pass ~2.3M (imminent in the live era); +500M clears NFL's
    # whole realistic span (200M+2099*1e5 ≈ 410M < 500M). See README §11. (2026-08-05)
    "ucl": {
        "base_url": "https://v3.football.api-sports.io",
        "league_id": 2,
        "odds_league_id": "UEFA_CHAMPIONS_LEAGUE",
        "id_offset": 500_000_000,
    },
    # NHL (hockey) — NHL's OWN free public API (api-web.nhle.com, key-less), NOT
    # API-Sports: their hockey API's current season is paid, NHL's own is free +
    # current (the MLB StatsAPI pattern). Odds via SGO free tier (odds_league_id
    # "NHL"). Ingested by ingestion/nhl_backfill.py.
    # id_offset is +1B, NOT the next +100M band: NHL native game ids are ~2.03e9
    # (season*1e6+...), already above every band and fitting INT4 (2.147e9) with no
    # room for a positive offset. nhl_backfill stores game_id = 1e9 + (raw - 2e9)
    # (see NHL_GAME_ID_EPOCH there); teams/players are 1e9 + raw. 1B clears NFL's
    # real 400-420M span + UCL 500M and stays under INT4. See README §11 / §16.4.
    "nhl": {
        "odds_league_id": "NHL",
        "id_offset": 1_000_000_000,
    },
}

NBA_LEAGUE_ID = SPORTS["nba"]["league_id"]
ODDS_NBA_LEAGUE_ID = SPORTS["nba"]["odds_league_id"]
