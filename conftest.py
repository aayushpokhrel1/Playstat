"""Root conftest — pytest imports this before collecting any test module.

ingestion/config.py reads DATABASE_URL and API_BASKETBALL_KEY from the
environment as *required* (os.environ[...], KeyError if absent) at import
time, after calling load_dotenv(). Locally that's fine because a .env file
exists, but CI has no .env, and several modules under test import
ingestion.config transitively (ingestion.db -> optimizer.parlay,
modeling.edges, modeling.train). Setting safe dummy values here — before
those imports happen — lets the whole suite import and run without a .env
or any real credentials. DATABASE_URL is never actually connected to: every
engine SQLAlchemy builds from it is lazy, and all tests in this suite are
pure-math (no DB access). ODDS_API_KEY is read with .get(), so it's already
optional and needs no default here.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://ci:ci@localhost:5432/ci_dummy")
os.environ.setdefault("API_BASKETBALL_KEY", "ci-dummy")
