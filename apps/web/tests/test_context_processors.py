import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string

from apps.utils.factories.notifications import EventUserFactory
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import UserFactory
from apps.web.context_processors import unread_notifications_count


@pytest.mark.django_db()
class TestUnreadNotificationsCountContextProcessor:
    def test_returns_the_all_teams_count_for_an_authenticated_user(self, rf):
        user = UserFactory.create()
        team_a = TeamFactory.create()
        team_b = TeamFactory.create()
        MembershipFactory.create(user=user, team=team_a)
        MembershipFactory.create(user=user, team=team_b)
        EventUserFactory.create(user=user, team=team_a, read=False)
        EventUserFactory.create(user=user, team=team_b, read=False)

        request = rf.get("/")
        request.user = user

        assert unread_notifications_count(request) == {"unread_notifications_count": 2}

    def test_returns_zero_for_an_anonymous_user(self, rf):
        request = rf.get("/")
        request.user = AnonymousUser()

        assert unread_notifications_count(request) == {"unread_notifications_count": 0}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("loader_key", "debug", "expect_script"),
    [
        pytest.param("abc123", False, True, id="key-set"),
        pytest.param("", False, False, id="key-empty"),
        pytest.param("abc123", True, False, id="debug"),
    ],
)
def test_sentry_browser_loader_script(rf, settings, loader_key, debug, expect_script):
    settings.SENTRY_BROWSER_LOADER_KEY = loader_key
    settings.DEBUG = debug
    request = rf.get("/")
    request.user = AnonymousUser()

    content = render_to_string("web/base.html", request=request)

    assert ("https://js.sentry-cdn.com/abc123.min.js" in content) is expect_script
    assert expect_script or "js.sentry-cdn.com" not in content
