"""Tests for TheOddsClient, mirroring test_ingestion_retry.py's Layer-1
network-retry hardening: `get()` must retry `requests.exceptions.ConnectionError`
and `requests.exceptions.Timeout` raised by `session.get(...)` itself, with the
same backoff cadence used for 5xx/429 status codes, while 401/403 fail fast
(no retry) and the metered `x-requests-*` headers are captured as
`last_headers`. Unlike SportsGameOdds, this API returns bare JSON arrays, so
there is no success-key check.

No DB, no real sockets: `client.session.get` is monkeypatched to a fake with
scripted side effects (raise an exception, or return a FakeResponse), and
`time.sleep` is monkeypatched to a no-op recorder so the whole suite runs
instantly. The root conftest.py supplies dummy DATABASE_URL/API_BASKETBALL_KEY
so ingestion.config (and the modules under test, which import it) can be
imported without a .env; THE_ODDS_API_KEY is read with .get() so it stays
None under test — no network call ever uses it.

Run with: python -m pytest tests/test_theodds_client.py -q
"""

import pytest
import requests

import ingestion.theodds_client as theodds_client
from ingestion.theodds_client import TheOddsAPIError, TheOddsClient


class FakeResponse:
    """Minimal stand-in for requests.Response used by the client under test."""

    def __init__(self, status_code, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json_data = [] if json_data is None else json_data
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


def test_theodds_connection_error_then_success(monkeypatch):
    client = TheOddsClient()
    sleep_calls = _no_op_sleep(monkeypatch, theodds_client)
    script = [
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom again"),
        FakeResponse(200, [{"id": 1}]),
    ]
    assert len(script) == theodds_client.MAX_RETRIES
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/sports")

    assert result == [{"id": 1}]
    assert calls["count"] == theodds_client.MAX_RETRIES
    assert sleep_calls


def test_theodds_timeout_every_attempt_reraises(monkeypatch):
    client = TheOddsClient()
    _no_op_sleep(monkeypatch, theodds_client)
    script = [requests.exceptions.Timeout("slow")] * theodds_client.MAX_RETRIES
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    with pytest.raises(requests.exceptions.Timeout):
        client.get("/sports")

    assert calls["count"] == theodds_client.MAX_RETRIES


def test_theodds_429_then_success_still_retries(monkeypatch):
    client = TheOddsClient()
    sleep_calls = _no_op_sleep(monkeypatch, theodds_client)
    script = [FakeResponse(429), FakeResponse(200, [{"id": 1}])]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/sports")

    assert result == [{"id": 1}]
    assert calls["count"] == 2
    assert sleep_calls


def test_theodds_403_no_retry(monkeypatch):
    client = TheOddsClient()
    _no_op_sleep(monkeypatch, theodds_client)
    script = [FakeResponse(403)]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    with pytest.raises(TheOddsAPIError, match="403 on /sports"):
        client.get("/sports")

    assert calls["count"] == 1


def test_theodds_last_headers_captures_requests_remaining(monkeypatch):
    client = TheOddsClient()
    _no_op_sleep(monkeypatch, theodds_client)
    headers = {
        "X-Requests-Remaining": "42",
        "X-Requests-Used": "3",
        "Content-Type": "application/json",
    }
    script = [FakeResponse(200, [{"id": 1}], headers=headers)]
    fake_get, calls = _scripted_get(script)
    monkeypatch.setattr(client.session, "get", fake_get)

    result = client.get("/sports")

    assert result == [{"id": 1}]
    assert client.last_headers["x-requests-remaining"] == "42"
    assert client.last_headers["x-requests-used"] == "3"
    assert "content-type" not in client.last_headers
    assert calls["count"] == 1
