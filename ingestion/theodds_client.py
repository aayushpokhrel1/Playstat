import time

import requests

from ingestion.config import THEODDS_API_BASE_URL, THEODDS_API_KEY

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
MIN_SECONDS_BETWEEN_REQUESTS = 1.0  # free tier is 500 credits/month; keep the pacing gentle


class TheOddsAPIError(Exception):
    """Raised when The Odds API returns an auth/plan error."""


class TheOddsClient:
    def __init__(self):
        self.session = requests.Session()
        self._last_request_at = 0
        self.last_headers = {}

    def _pace(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_SECONDS_BETWEEN_REQUESTS:
            time.sleep(MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
        self._last_request_at = time.monotonic()

    def get(self, path, params=None):
        url = f"{THEODDS_API_BASE_URL}{path}"
        params = dict(params or {})
        params["apiKey"] = THEODDS_API_KEY

        for attempt in range(1, MAX_RETRIES + 1):
            self._pace()
            try:
                response = self.session.get(url, params=params, timeout=(10, 30))
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                    continue
                raise

            if response.status_code == 429 and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if response.status_code >= 500 and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if response.status_code in (401, 403):
                raise TheOddsAPIError(
                    f"{response.status_code} on {path} — check THE_ODDS_API_KEY / plan tier"
                )

            response.raise_for_status()
            self.last_headers = {
                k.lower(): v
                for k, v in response.headers.items()
                if k.lower().startswith("x-requests")
            }
            return response.json()

        raise RuntimeError(f"Exhausted retries fetching {path}")
