import time

import requests

from ingestion.config import ODDS_API_BASE_URL, ODDS_API_KEY

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
MIN_SECONDS_BETWEEN_REQUESTS = 6.5  # stay under the free tier's 10-requests/minute cap


class OddsAPIError(Exception):
    """Raised when SportsGameOdds returns an error response."""


class SportsGameOddsClient:
    def __init__(self):
        self.session = requests.Session()
        self._last_request_at = 0

    def _pace(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_SECONDS_BETWEEN_REQUESTS:
            time.sleep(MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
        self._last_request_at = time.monotonic()

    def get(self, path, params=None):
        url = f"{ODDS_API_BASE_URL}{path}"
        params = dict(params or {})
        params["apiKey"] = ODDS_API_KEY

        for attempt in range(1, MAX_RETRIES + 1):
            self._pace()
            response = self.session.get(url, params=params, timeout=15)

            if response.status_code == 429 and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if response.status_code >= 500 and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if response.status_code == 403:
                raise OddsAPIError(
                    f"403 Forbidden on {path} — likely a plan/tier restriction."
                )

            response.raise_for_status()
            payload = response.json()
            if not payload.get("success", True):
                raise OddsAPIError(f"SportsGameOdds error on {path}: {payload}")

            return payload

        raise RuntimeError(f"Exhausted retries fetching {path}")

    def get_events(self, league_id, odds_available=True):
        """Yields events across all pages for a league."""
        cursor = None
        while True:
            params = {"leagueID": league_id}
            if odds_available:
                params["oddsAvailable"] = "true"
            if cursor:
                params["cursor"] = cursor

            payload = self.get("/events/", params=params)
            for event in payload.get("data", []):
                yield event

            cursor = payload.get("nextCursor")
            if not cursor:
                break
