import pytest
import requests

from apps.teams.sync.client import SourceClient


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.closed = False

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _client(session):
    return SourceClient("https://src.example/", "secret-key", session=session, sleep=lambda _s: None)


def test_get_manifest_hits_endpoint_with_api_key_header():
    session = FakeSession([FakeResponse(json_data={"entries": []})])
    client = _client(session)
    assert client.get_manifest() == {"entries": []}
    call = session.calls[0]
    assert call["url"] == "https://src.example/api/v2/manifest/"
    assert call["headers"]["X-Api-Key"] == "secret-key"


def test_get_page_passes_cursor_and_limit():
    session = FakeSession([FakeResponse(json_data={"results": [], "has_more": False, "cursor": "9"})])
    _client(session).get_page("teams", cursor="4", limit=50)
    call = session.calls[0]
    assert call["url"] == "https://src.example/api/v2/teams/"
    assert call["params"] == {"cursor": "4", "limit": 50}


def test_iter_rows_follows_has_more():
    session = FakeSession(
        [
            FakeResponse(json_data={"results": [{"id": 1}], "has_more": True, "cursor": "1"}),
            FakeResponse(json_data={"results": [{"id": 2}], "has_more": False, "cursor": "2"}),
        ]
    )
    rows = list(_client(session).iter_rows("teams"))
    assert [r["id"] for r in rows] == [1, 2]
    assert session.calls[1]["params"]["cursor"] == "1"


def test_transient_5xx_is_retried_then_succeeds():
    failed = FakeResponse(503)
    session = FakeSession([failed, FakeResponse(json_data={"ok": True})])
    assert _client(session).get_manifest() == {"ok": True}
    assert len(session.calls) == 2
    assert failed.closed  # the 5xx response is released back to the pool before retrying


def test_connection_error_is_retried():
    session = FakeSession([requests.ConnectionError("boom"), FakeResponse(json_data={"ok": True})])
    assert _client(session).get_manifest() == {"ok": True}
    assert len(session.calls) == 2


def test_retries_are_exhausted_and_raise():
    session = FakeSession([FakeResponse(503)] * 10)
    with pytest.raises(requests.HTTPError):
        _client(session).get_manifest()


def test_client_error_is_not_retried():
    session = FakeSession([FakeResponse(403)])
    with pytest.raises(requests.HTTPError):
        _client(session).get_manifest()
    assert len(session.calls) == 1
