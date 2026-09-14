from unittest.mock import MagicMock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse

from apps.web.cache_control_middleware import SensitiveDataCacheControlMiddleware


@pytest.fixture()
def request_factory():
    return RequestFactory()


class TestSensitiveDataCacheControlMiddleware:
    def _middleware(self, response):
        get_response = MagicMock(return_value=response)
        return SensitiveDataCacheControlMiddleware(get_response)

    def test_adds_no_store_for_authenticated_user(self, request_factory):
        request = request_factory.get("/some/path")
        request.user = MagicMock(is_anonymous=False)
        response = HttpResponse()

        result = self._middleware(response)(request)

        cache_control = result["Cache-Control"]
        assert "no-store" in cache_control
        assert "no-cache" in cache_control
        assert "must-revalidate" in cache_control
        assert "private" in cache_control

    def test_leaves_anonymous_responses_untouched(self, request_factory):
        request = request_factory.get("/some/path")
        request.user = AnonymousUser()
        response = HttpResponse()

        result = self._middleware(response)(request)

        assert "Cache-Control" not in result

    def test_does_not_override_a_views_own_cache_control(self, request_factory):
        """A view that already made its own caching decision (e.g. the experiment trend
        chart's deliberate private, max-age browser cache) is left alone rather than having
        no-store layered on top, which would defeat the point of that caching."""
        request = request_factory.get("/some/path")
        request.user = MagicMock(is_anonymous=False)
        response = HttpResponse()
        response["Cache-Control"] = "max-age=900, private"

        result = self._middleware(response)(request)

        assert result["Cache-Control"] == "max-age=900, private"


@pytest.mark.django_db()
class TestSensitiveDataCacheControlMiddlewareIntegration:
    """Proves the middleware is actually wired into MIDDLEWARE, not just correct in isolation."""

    def test_authenticated_page_gets_no_store(self, client, team_with_users):
        user = team_with_users.members.first()
        client.login(username=user.username, password="password")

        url = reverse("participants:participant_home", args=[team_with_users.slug])
        response = client.get(url)

        assert response.status_code == 200
        assert "no-store" in response["Cache-Control"]

    def test_does_not_override_the_trend_chart_own_cache_control(self, client, experiment):
        """The one real view in the codebase that sets its own Cache-Control (a deliberate
        browser-side cache for the trend chart) must keep exactly that header, not have
        no-store layered on top by this middleware."""
        team = experiment.team
        user = team.members.first()
        client.login(username=user.username, password="password")

        url = reverse("experiments:trends_data", args=[team.slug, experiment.id])
        response = client.get(url)

        assert response.status_code == 200
        assert response["Cache-Control"] == "max-age=900, private"
