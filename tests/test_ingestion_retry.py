"""Tests for Layer 1 of the ingestion network-retry hardening
(docs/superpowers/plans/2026-07-28-ingestion-network-retry.md): each ingestion
HTTP client's `get()` must retry `requests.exceptions.ConnectionError` and
`requests.exceptions.Timeout` raised by `session.get(...)` itself, with the
same backoff cadence already used for 5xx/429 status codes, while leaving 4xx
regression behavior (fail fast, no retry) and existing status-code retries
intact.

No DB, no real sockets: `client.session.get` is monkeypatched to a fake with
scripted side effects (raise an exception, or return a FakeResponse), and
`time.sleep` is monkeypatched to a no-op recorder so the whole suite runs
instantly. The root conftest.py supplies dummy DATABASE_URL/API_BASKETBALL_KEY
so ingestion.config (and the modules under test, which import it) can be
imported without a .env.

Run with: python -m pytest tests/test_ingestion_retry.py -q
"""

import pytest
import requests

import ingestion.api_client as api_client
import ingestion.mlb_backfill as mlb_backfill
import ingestion.nfl_backfill as nfl_backfill
import ingestion.odds_client as odds_client
from ingestion.api_client import APISportsClient
from ingestion.mlb_backfill import MLBStatsClient
from ingestion.odds_client import SportsGameOddsClient


class FakeResponse:
    """Minimal stand-in for requests.Response used by all clients under test."""

    def __init__(self, status_code, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json_data = {} if json_data is None else json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")


def _scripted_get(script):
    """Returns a fake `session.get(...)` that pops one entry per call from
    `script` (a list of exceptions to raise or FakeResponses to return) and a
    dict tracking how many times it was called.
    """
    calls = {"count": 0}

    def _fake_get(*args, **kwargs):
        calls["count"] += 1
        item = script[calls["count"] - 1]
        if isinstance(item, Exception):
            raise item
        return item

    return _fake_get, calls


def _no_op_sleep(monkeypatch, module):
    """Patches `module.time.sleep` to a no-op that records its args, so
    backoff/pacing delays don't actually elapse during the test.
    """
    sleep_calls = []
    monkeypatch.setattr(module.time, "sleep", lambda s: sleep_calls.append(s))
    return sleep_calls


# --- MLBStatsClient --------------------------------------------------------

def test_mlb_connection_error_then_success(monkeypatch):
    client = MLBStatsClient()
    sleep_calls = _no_op_sleep(monkeypatch, mlb_backfill)
    script = [
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom again"),
        FakeResponse(200, {"ok": True}),
    ]
    assert len(script) == mlb_backfill.MAX_RETRIES
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/teams")

    assert result == {"ok": True}
    assert calls["count"] == mlb_backfill.MAX_RETRIES
    assert sleep_calls


def test_mlb_timeout_then_success(monkeypatch):
    client = MLBStatsClient()
    sleep_calls = _no_op_sleep(monkeypatch, mlb_backfill)
    script = [
        requests.exceptions.Timeout("slow"),
        requests.exceptions.Timeout("slow again"),
        FakeResponse(200, {"ok": True}),
    ]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/teams")

    assert result == {"ok": True}
    assert calls["count"] == mlb_backfill.MAX_RETRIES
    assert sleep_calls


def test_mlb_connection_error_every_attempt_reraises(monkeypatch):
    client = MLBStatsClient()
    _no_op_sleep(monkeypatch, mlb_backfill)
    script = [requests.exceptions.ConnectionError("boom")] * mlb_backfill.MAX_RETRIES
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    with pytest.raises(requests.exceptions.ConnectionError):
        client.get("/teams")

    assert calls["count"] == mlb_backfill.MAX_RETRIES


def test_mlb_404_not_retried(monkeypatch):
    client = MLBStatsClient()
    _no_op_sleep(monkeypatch, mlb_backfill)
    script = [FakeResponse(404)]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    with pytest.raises(requests.exceptions.HTTPError):
        client.get("/teams")

    assert calls["count"] == 1


def test_mlb_5xx_then_success_still_retries(monkeypatch):
    client = MLBStatsClient()
    sleep_calls = _no_op_sleep(monkeypatch, mlb_backfill)
    script = [FakeResponse(503), FakeResponse(200, {"ok": True})]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/teams")

    assert result == {"ok": True}
    assert calls["count"] == 2
    assert sleep_calls


# --- SportsGameOddsClient ---------------------------------------------------

def test_odds_connection_error_then_success(monkeypatch):
    client = SportsGameOddsClient()
    sleep_calls = _no_op_sleep(monkeypatch, odds_client)
    script = [
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom again"),
        FakeResponse(200, {"success": True, "data": []}),
    ]
    assert len(script) == odds_client.MAX_RETRIES
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/events/")

    assert result == {"success": True, "data": []}
    assert calls["count"] == odds_client.MAX_RETRIES
    assert sleep_calls


def test_odds_timeout_then_success(monkeypatch):
    client = SportsGameOddsClient()
    sleep_calls = _no_op_sleep(monkeypatch, odds_client)
    script = [
        requests.exceptions.Timeout("slow"),
        requests.exceptions.Timeout("slow again"),
        FakeResponse(200, {"success": True, "data": []}),
    ]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/events/")

    assert result == {"success": True, "data": []}
    assert calls["count"] == odds_client.MAX_RETRIES
    assert sleep_calls


def test_odds_connection_error_every_attempt_reraises(monkeypatch):
    client = SportsGameOddsClient()
    _no_op_sleep(monkeypatch, odds_client)
    script = [requests.exceptions.ConnectionError("boom")] * odds_client.MAX_RETRIES
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    with pytest.raises(requests.exceptions.ConnectionError):
        client.get("/events/")

    assert calls["count"] == odds_client.MAX_RETRIES


def test_odds_404_not_retried(monkeypatch):
    client = SportsGameOddsClient()
    _no_op_sleep(monkeypatch, odds_client)
    script = [FakeResponse(404)]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    with pytest.raises(requests.exceptions.HTTPError):
        client.get("/events/")

    assert calls["count"] == 1


def test_odds_5xx_then_success_still_retries(monkeypatch):
    client = SportsGameOddsClient()
    sleep_calls = _no_op_sleep(monkeypatch, odds_client)
    script = [FakeResponse(503), FakeResponse(200, {"success": True, "data": []})]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/events/")

    assert result == {"success": True, "data": []}
    assert calls["count"] == 2
    assert sleep_calls


def test_odds_429_then_success_still_retries(monkeypatch):
    client = SportsGameOddsClient()
    sleep_calls = _no_op_sleep(monkeypatch, odds_client)
    script = [FakeResponse(429), FakeResponse(200, {"success": True, "data": []})]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/events/")

    assert result == {"success": True, "data": []}
    assert calls["count"] == 2
    assert sleep_calls


# --- APISportsClient ---------------------------------------------------------

def test_api_connection_error_then_success(monkeypatch):
    client = APISportsClient(sport="nba")
    sleep_calls = _no_op_sleep(monkeypatch, api_client)
    script = [
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom again"),
        FakeResponse(200, {"response": [{"id": 1}]}),
    ]
    assert len(script) == api_client.MAX_RETRIES
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/games")

    assert result == [{"id": 1}]
    assert calls["count"] == api_client.MAX_RETRIES
    assert sleep_calls


def test_api_timeout_then_success(monkeypatch):
    client = APISportsClient(sport="nba")
    sleep_calls = _no_op_sleep(monkeypatch, api_client)
    script = [
        requests.exceptions.Timeout("slow"),
        requests.exceptions.Timeout("slow again"),
        FakeResponse(200, {"response": [{"id": 1}]}),
    ]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/games")

    assert result == [{"id": 1}]
    assert calls["count"] == api_client.MAX_RETRIES
    assert sleep_calls


def test_api_connection_error_every_attempt_reraises(monkeypatch):
    client = APISportsClient(sport="nba")
    _no_op_sleep(monkeypatch, api_client)
    script = [requests.exceptions.ConnectionError("boom")] * api_client.MAX_RETRIES
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    with pytest.raises(requests.exceptions.ConnectionError):
        client.get("/games")

    assert calls["count"] == api_client.MAX_RETRIES


def test_api_404_not_retried(monkeypatch):
    client = APISportsClient(sport="nba")
    _no_op_sleep(monkeypatch, api_client)
    script = [FakeResponse(404)]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    with pytest.raises(requests.exceptions.HTTPError):
        client.get("/games")

    assert calls["count"] == 1


def test_api_5xx_then_success_still_retries(monkeypatch):
    client = APISportsClient(sport="nba")
    sleep_calls = _no_op_sleep(monkeypatch, api_client)
    script = [FakeResponse(503), FakeResponse(200, {"response": [{"id": 1}]})]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/games")

    assert result == [{"id": 1}]
    assert calls["count"] == 2
    assert sleep_calls


def test_api_429_minute_throttle_then_success_still_retries(monkeypatch):
    # A 429 with no x-ratelimit-requests-remaining header is the per-minute
    # throttle (not the daily-quota-exhaustion path) and must still retry.
    client = APISportsClient(sport="nba")
    sleep_calls = _no_op_sleep(monkeypatch, api_client)
    script = [FakeResponse(429), FakeResponse(200, {"response": [{"id": 1}]})]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/games")

    assert result == [{"id": 1}]
    assert calls["count"] == 2
    assert sleep_calls


# --- nfl_backfill._fetch_csv --------------------------------------------------

def test_nfl_fetch_csv_connection_error_then_success(monkeypatch):
    # `_fetch_csv` uses module-level requests.get (not a session), so the
    # scripted fake patches nfl_backfill.requests.get directly.
    sleep_calls = _no_op_sleep(monkeypatch, nfl_backfill)
    script = [
        requests.exceptions.ConnectionError("boom"),
        FakeResponse(200, text="a,b\n1,2\n"),
    ]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(nfl_backfill.requests, "get", fake_get)

    rows = nfl_backfill._fetch_csv("https://example.com/games.csv")

    assert rows == [{"a": "1", "b": "2"}]
    assert calls["count"] == 2
    assert sleep_calls


def test_nfl_fetch_csv_timeout_then_success(monkeypatch):
    sleep_calls = _no_op_sleep(monkeypatch, nfl_backfill)
    script = [
        requests.exceptions.Timeout("slow"),
        requests.exceptions.Timeout("slow again"),
        FakeResponse(200, text="a,b\n1,2\n"),
    ]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(nfl_backfill.requests, "get", fake_get)

    rows = nfl_backfill._fetch_csv("https://example.com/games.csv")

    assert rows == [{"a": "1", "b": "2"}]
    assert calls["count"] == 3
    assert sleep_calls


def test_nfl_fetch_csv_connection_error_every_attempt_reraises(monkeypatch):
    _no_op_sleep(monkeypatch, nfl_backfill)
    script = [requests.exceptions.ConnectionError("boom")] * nfl_backfill.MAX_RETRIES
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(nfl_backfill.requests, "get", fake_get)

    with pytest.raises(requests.exceptions.ConnectionError):
        nfl_backfill._fetch_csv("https://example.com/games.csv")

    assert calls["count"] == nfl_backfill.MAX_RETRIES


def test_nfl_fetch_csv_404_not_retried(monkeypatch):
    _no_op_sleep(monkeypatch, nfl_backfill)
    script = [FakeResponse(404)]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(nfl_backfill.requests, "get", fake_get)

    with pytest.raises(requests.exceptions.HTTPError):
        nfl_backfill._fetch_csv("https://example.com/games.csv")

    assert calls["count"] == 1


def test_nfl_fetch_csv_timeout_is_connect_read_tuple(monkeypatch):
    _no_op_sleep(monkeypatch, nfl_backfill)
    captured = {}

    def _fake_get(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return FakeResponse(200, text="a,b\n1,2\n")

    monkeypatch.setattr(nfl_backfill.requests, "get", _fake_get)

    rows = nfl_backfill._fetch_csv("https://example.com/games.csv")

    assert rows == [{"a": "1", "b": "2"}]
    assert captured["timeout"] == (
        nfl_backfill.CONNECT_TIMEOUT_SECONDS,
        nfl_backfill.READ_TIMEOUT_SECONDS,
    )
