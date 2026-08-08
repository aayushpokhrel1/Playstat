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
            try:
                response = self.session.get(url, params=params, timeout=15)
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

    def get_events(self, league_id, odds_available=True, starts_after=None,
                   starts_before=None, limit=None):
        """Yields events across all pages for a league.

        starts_after/starts_before (UTC ISO-8601) narrow the pull to one slate
        window. This is a QUOTA control, not a convenience: the free tier meters
        entities (1 returned event = 1 entity, 2,500/month) and an unfiltered MLB
        pull returns ~51 events, ~70% of them future-dated games the builder
        discards. See ingestion/slate_window.py and README §15.9 item 11.

        limit sets the page size; limit=100 makes a narrowed slate fit in ONE
        request instead of paging at 6.5s/request (the 2026-08-08 odds step took
        885s largely for this reason).

        Defaults are all None/unset, so the unfiltered call is byte-identical.
        """
        cursor = None
        while True:
            params = {"leagueID": league_id}
            if odds_available:
                params["oddsAvailable"] = "true"
            if starts_after:
                params["startsAfter"] = starts_after
            if starts_before:
                params["startsBefore"] = starts_before
            if limit:
                params["limit"] = limit
            if cursor:
                params["cursor"] = cursor

            payload = self.get("/events/", params=params)
            for event in payload.get("data", []):
                yield event

            cursor = payload.get("nextCursor")
            if not cursor:
                break
