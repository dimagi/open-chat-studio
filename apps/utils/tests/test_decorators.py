from datetime import UTC, datetime

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from apps.utils.decorators import sunset

SUNSET_AT = datetime(2026, 9, 1, tzinfo=UTC)


def _make_view(**sunset_kwargs):
    @sunset(SUNSET_AT, **sunset_kwargs)
    def view(request):
        return HttpResponse("ok")

    return view


def test_sunset_adds_deprecation_and_sunset_headers():
    response = _make_view()(RequestFactory().get("/"))
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"] == "Tue, 01 Sep 2026 00:00:00 GMT"
    assert "Link" not in response.headers


def test_sunset_without_successor_leaves_body_and_status_untouched():
    response = _make_view()(RequestFactory().get("/"))
    assert response.status_code == 200
    assert response.content == b"ok"


def test_sunset_advertises_successor_url():
    view = _make_view(successor_url="https://example.com/chat/widget/")
    response = view(RequestFactory().get("/"))
    assert response.headers["Link"] == '<https://example.com/chat/widget/>; rel="successor-version"'


def test_sunset_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        sunset(datetime(2026, 9, 1))
