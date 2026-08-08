from ingestion.odds_client import SportsGameOddsClient


class _RecordingClient(SportsGameOddsClient):
    """Captures the params of each .get() instead of hitting the network."""

    def __init__(self, pages):
        self.calls = []
        self._pages = list(pages)

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        return self._pages.pop(0)


def test_get_events_omits_window_params_by_default():
    client = _RecordingClient([{"data": [{"eventID": "a"}]}])
    assert len(list(client.get_events("MLB"))) == 1
    _, params = client.calls[0]
    assert "startsAfter" not in params
    assert "startsBefore" not in params
    assert "limit" not in params


def test_get_events_passes_window_and_limit_through():
    client = _RecordingClient([{"data": []}])
    list(client.get_events(
        "MLB",
        starts_after="2026-08-08T10:00:00Z",
        starts_before="2026-08-09T10:00:00Z",
        limit=100,
    ))
    _, params = client.calls[0]
    assert params["startsAfter"] == "2026-08-08T10:00:00Z"
    assert params["startsBefore"] == "2026-08-09T10:00:00Z"
    assert params["limit"] == 100


def test_get_events_keeps_window_params_on_every_page():
    client = _RecordingClient([
        {"data": [{"eventID": "a"}], "nextCursor": "c1"},
        {"data": [{"eventID": "b"}]},
    ])
    events = list(client.get_events("MLB", starts_after="2026-08-08T10:00:00Z", limit=100))
    assert [e["eventID"] for e in events] == ["a", "b"]
    assert len(client.calls) == 2
    # A dropped filter on page 2 would silently re-bill the full unfiltered slate.
    assert client.calls[1][1]["startsAfter"] == "2026-08-08T10:00:00Z"
    assert client.calls[1][1]["limit"] == 100
    assert client.calls[1][1]["cursor"] == "c1"
