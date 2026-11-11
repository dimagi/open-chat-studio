"""Every v2 endpoint that writes to a chatbot is held to the application's allowlist (#4552).

There are two ways a new endpoint can escape the gate: leave `ApplicationAllowsChatbot` off its
permissions, or declare it and then resolve the chatbot without running object permissions. One
test per way. Neither names an endpoint, so a new chatbot write route is covered the moment it is
registered, and passes once it declares the class and resolves through `get_authorized_chatbot`.
"""

from pathlib import Path

import pytest
from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

import apps.api.v2
from apps.api.v2 import urls as v2_urls
from apps.api.v2.permissions import ApplicationAllowsChatbot
from apps.utils.tests.url_helpers import iter_view_callbacks

#: Reads held to the write allowlist on purpose: a token that may not publish has no publish to follow.
GATED_READS = {"chatbots/<str:id>/versions/status/": {"GET"}}

#: How a route under ``/chatbots/{id}/`` spells its parameter: ``path()`` on the one hand, the
#: router's compiled regex on the other.
CHATBOT_ROUTE_MARKERS = ("chatbots/<str:id>/", "chatbots/(?P<id>")


def _allowed_methods(callback) -> set[str]:
    """The verbs a DRF view serves on this route, OPTIONS and HEAD excluded."""
    if actions := getattr(callback, "actions", None):
        methods = set(actions)
    else:
        cls, initkwargs = callback.cls, callback.initkwargs
        names = initkwargs.get("http_method_names", cls.http_method_names)
        methods = {name for name in names if hasattr(cls, name)}
    return {method.upper() for method in methods} - {"OPTIONS", "HEAD"}


def _permissions_for(callback, method: str) -> list:
    view = callback.cls(**callback.initkwargs)
    view.request = Request(APIRequestFactory().generic(method, "/"))
    if actions := getattr(callback, "actions", None):
        view.action = actions[method.lower()]
    return view.get_permissions()


def _chatbot_write_routes():
    for route, callback in iter_view_callbacks(v2_urls.urlpatterns):
        if not any(marker in route for marker in CHATBOT_ROUTE_MARKERS):
            continue
        allowed = _allowed_methods(callback)
        required = {method for method in allowed if method not in SAFE_METHODS} | GATED_READS.get(route, set())
        for method in sorted(required & allowed):
            yield pytest.param(callback, method, id=f"{callback.cls.__name__}:{method}")


CHATBOT_WRITE_ROUTES = list(_chatbot_write_routes())


def test_the_walk_finds_the_chatbot_write_routes():
    """Guards the guard: if the url layout changes shape, this fails rather than the walk going quiet."""
    assert len(CHATBOT_WRITE_ROUTES) >= 8


@pytest.mark.parametrize(("callback", "method"), CHATBOT_WRITE_ROUTES)
def test_every_chatbot_write_declares_the_allowlist_permission(callback, method):
    permissions = _permissions_for(callback, method)

    assert any(isinstance(permission, ApplicationAllowsChatbot) for permission in permissions), (
        f"{callback.cls.__name__} serves {method} on a chatbot without declaring ApplicationAllowsChatbot"
    )


def test_views_resolve_chatbots_through_the_authorized_lookup_only():
    """`get_working_chatbot` runs no object permissions; outside `lookups.py` the resolver is the only entry."""
    package = Path(apps.api.v2.__file__).parent
    offenders = sorted(
        str(source.relative_to(package))
        for source in package.rglob("*.py")
        if "tests" not in source.parts and source.name != "lookups.py" and "get_working_chatbot(" in source.read_text()
    )

    assert offenders == []
