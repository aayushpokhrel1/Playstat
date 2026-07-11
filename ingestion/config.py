import os

from dotenv import load_dotenv

load_dotenv()

API_BASKETBALL_KEY = os.environ["API_BASKETBALL_KEY"]
API_BASKETBALL_BASE_URL = "https://v1.basketball.api-sports.io"

ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
ODDS_API_BASE_URL = "https://api.sportsgameodds.com/v2"

DATABASE_URL = os.environ["DATABASE_URL"]

NBA_LEAGUE_ID = 12
ODDS_NBA_LEAGUE_ID = "NBA"
