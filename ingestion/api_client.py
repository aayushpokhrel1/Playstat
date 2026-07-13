import time

import requests

from ingestion.config import API_BASKETBALL_KEY, SPORTS

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
PER_MINUTE_THROTTLE_SLEEP_SECONDS = 61
MIN_SECONDS_BETWEEN_REQUESTS = 6.5  # stay under the 10-requests/minute cap


class QuotaExhaustedError(Exception):
    """Raised when the API-Sports daily request quota is used up."""


class APISportsClient:
    """Client for any API-Sports per-sport API — they share auth, rate-limit
    headers, and response envelope; only the base URL differs per sport.
    """

    def __init__(self, sport="nba"):
        self.sport = sport
        self.base_url = SPORTS[sport]["base_url"]
        self.session = requests.Session()
        self.session.headers.update({"x-apisports-key": API_BASKETBALL_KEY})
        self._last_request_at = 0

    def _pace(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_SECONDS_BETWEEN_REQUESTS:
            time.sleep(MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
        self._last_request_at = time.monotonic()

    def get(self, path, params=None):
        url = f"{self.base_url}{path}"

        for attempt in range(1, MAX_RETRIES + 1):
            self._pace()
            response = self.session.get(url, params=params, timeout=15)

            if response.status_code == 429:
                daily_remaining = response.headers.get("x-ratelimit-requests-remaining")
                if daily_remaining is not None and int(daily_remaining) <= 0:
                    raise QuotaExhaustedError(
                        f"Daily API-Sports quota exhausted on {path}."
                    )
                # Otherwise this is the 10-requests/minute throttle, not the daily cap.
                time.sleep(PER_MINUTE_THROTTLE_SLEEP_SECONDS)
                continue

            daily_remaining = response.headers.get("x-ratelimit-requests-remaining")
            if daily_remaining is not None and int(daily_remaining) <= 0:
                raise QuotaExhaustedError(
                    f"Daily API-Sports quota exhausted after {path}."
                )

            if response.status_code >= 500 and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            response.raise_for_status()
            payload = response.json()

            errors = payload.get("errors")
            if errors:
                if isinstance(errors, dict) and "rateLimit" in errors and attempt < MAX_RETRIES:
                    time.sleep(PER_MINUTE_THROTTLE_SLEEP_SECONDS)
                    continue
                if isinstance(errors, dict) and "requests" in errors:
                    raise QuotaExhaustedError(
                        f"Daily API-Sports quota exhausted on {path}: {errors['requests']}"
                    )
                raise RuntimeError(f"API-Sports error on {path}: {errors}")

            return payload.get("response", [])

        raise RuntimeError(f"Exhausted retries fetching {path}")

# Backwards-compatible alias from the basketball-only era.
APIBasketballClient = APISportsClient
