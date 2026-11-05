import json

import pytest
from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.http import HttpResponse
from django.test import RequestFactory
from django_htmx.http import HttpResponseClientRedirect, HttpResponseClientRefresh
from django_htmx.middleware import HtmxDetails

from apps.web.htmx_middleware import HtmxMessageMiddleware


def _htmx_request():
    request = RequestFactory().post("/", headers={"HX-Request": "true"})
    request.htmx = HtmxDetails(request)
    request._messages = CookieStorage(request)
    return request


def _run_middleware(request, response):
    def get_response(request):
        messages.success(request, "Done")
        return response

    return HtmxMessageMiddleware(get_response)(request)


def test_sends_messages_as_client_event():
    request = _htmx_request()

    response = _run_middleware(request=request, response=HttpResponse())

    trigger = json.loads(response.headers["HX-Trigger"])
    assert trigger["djangoMessages"]["messages"] == [{"message": "Done", "tags": "success"}]
    assert get_messages(request).used


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(HttpResponseClientRedirect("/next/"), id="client-redirect"),
        pytest.param(HttpResponseClientRefresh(), id="client-refresh"),
    ],
)
def test_keeps_messages_for_the_next_page_when_the_client_navigates(response):
    """The client leaves the page, so a toast would be lost; the next page renders the messages instead."""
    request = _htmx_request()

    response = _run_middleware(request=request, response=response)

    assert "HX-Trigger" not in response.headers
    assert not get_messages(request).used
