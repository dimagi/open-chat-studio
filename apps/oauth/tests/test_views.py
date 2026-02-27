import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from apps.oauth.models import OAuth2Application
from apps.oauth.views import ApplicationTableView, EditApplication, TeamScopedAuthorizationView
from apps.teams.helpers import create_default_team_for_user
from apps.teams.models import Team
from apps.utils.factories.team import MembershipFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def request_factory():
    """Factory for creating HTTP requests."""
    return RequestFactory()


@pytest.fixture()
def user_with_team(db):
    """Create a user with a default team."""
    user = UserFactory()
    create_default_team_for_user(user, "User's Team")  # ty: ignore[invalid-argument-type]
    return user


@pytest.fixture()
def user_without_team(db):
    """Create a user without any teams."""
    return UserFactory()


@pytest.fixture()
def view_with_oauth2_data():
    """Create a TeamScopedAuthorizationView instance with mocked oauth2_data."""
    view = TeamScopedAuthorizationView()
    view.oauth2_data = {}
    return view


@pytest.fixture()
def get_request_with_user(request_factory):
    """Factory fixture to create GET requests with user attached."""

    def _create_request(url="/", user=None):
        request = request_factory.get(url)
        request.user = user
        request.method = "GET"
        # Add session middleware
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()
        return request

    return _create_request


@pytest.fixture()
def post_request_with_user(request_factory):
    """Factory fixture to create POST requests with user attached."""

    def _create_request(url="/", user=None):
        request = request_factory.post(url)
        request.user = user
        request.method = "POST"
        # Add session middleware
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()
        return request

    return _create_request


@pytest.fixture()
def request_with_session(request_factory):
    """Factory fixture to create requests with session middleware attached."""

    def _create_request(url="/", user=None, method="GET"):
        if method == "GET":
            request = request_factory.get(url)
        else:
            request = request_factory.post(url)

        # Add session middleware
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        request.user = user
        request.method = method
        return request

    return _create_request


@pytest.fixture()
def oauth_applications_for_multiple_users(db, user_with_team):
    """Create OAuth applications for multiple users."""
    other_user = UserFactory()

    app_for_current_user = OAuth2Application.objects.create(
        name="Current User App",
        user=user_with_team,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
    )
    app_for_other_user = OAuth2Application.objects.create(
        name="Other User App",
        user=other_user,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
    )

    return {
        "current_user": user_with_team,
        "other_user": other_user,
        "current_user_app": app_for_current_user,
        "other_user_app": app_for_other_user,
    }


@pytest.mark.django_db()
def test_get_initial_with_team_parameter_user_is_member(get_request_with_user, user_with_team, view_with_oauth2_data):
    """Test that team_slug is set from URL parameter when user is a member."""
    user_team = user_with_team.teams.first()
    request = get_request_with_user(f"/?team={user_team.slug}", user_with_team)
    view_with_oauth2_data.request = request

    initial = view_with_oauth2_data.get_initial()

    assert initial["team_slug"] == user_team.slug


@pytest.mark.django_db()
def test_get_initial_with_team_parameter_user_not_member(get_request_with_user, user_with_team, view_with_oauth2_data):
    """Test that team_slug falls back to default when user is not a member of requested team."""
    # Create a team that the user is not a member of
    Team.objects.create(name="Other Team", slug="other-team")
    request = get_request_with_user("/?team=other-team", user_with_team)
    view_with_oauth2_data.request = request

    initial = view_with_oauth2_data.get_initial()

    # Should fall back to default team
    assert initial["team_slug"] == user_with_team.teams.first().slug


@pytest.mark.django_db()
def test_get_initial_without_team_parameter_uses_default(get_request_with_user, user_with_team, view_with_oauth2_data):
    """Test that default team is used when no team parameter is provided."""
    request = get_request_with_user("/", user_with_team)
    view_with_oauth2_data.request = request

    initial = view_with_oauth2_data.get_initial()

    assert initial["team_slug"] == user_with_team.teams.first().slug


@pytest.mark.django_db()
def test_get_initial_with_session_team(request_with_session, user_with_team, view_with_oauth2_data):
    """Test that team from session is used when available."""
    user_team = user_with_team.teams.first()
    request = request_with_session("/", user_with_team, "GET")
    request.session["team"] = user_team.id
    view_with_oauth2_data.request = request

    initial = view_with_oauth2_data.get_initial()

    assert initial["team_slug"] == user_team.slug


@pytest.mark.django_db()
def test_get_initial_with_multiple_teams_respects_parameter(
    get_request_with_user, user_with_team, view_with_oauth2_data
):
    """Test that team parameter is respected when user is member of multiple teams."""
    # Create another team and add user to it
    other_team = Team.objects.create(name="Other Team", slug="other-team")
    MembershipFactory(team=other_team, user=user_with_team)

    request = get_request_with_user("/?team=other-team", user_with_team)
    view_with_oauth2_data.request = request

    initial = view_with_oauth2_data.get_initial()

    assert initial["team_slug"] == "other-team"


@pytest.mark.django_db()
def test_requested_team_returns_valid_user_team(get_request_with_user, user_with_team, view_with_oauth2_data):
    """Test that requested_team returns a team when one was requested via URL parameter
    and the user is a member of that team."""
    user_team = user_with_team.teams.first()
    request = get_request_with_user(f"/?team={user_team.slug}", user_with_team)
    view_with_oauth2_data.request = request

    assert view_with_oauth2_data.requested_team == user_team


@pytest.mark.django_db()
def test_requested_team_returns_none_without_parameter(get_request_with_user, user_with_team, view_with_oauth2_data):
    """Test that requested_team returns None when no team parameter is provided."""
    request = get_request_with_user("/", user_with_team)
    view_with_oauth2_data.request = request

    assert view_with_oauth2_data.requested_team is None


@pytest.mark.django_db()
def test_requested_team_returns_none_user_not_member(get_request_with_user, user_with_team, view_with_oauth2_data):
    """Test that requested_team returns None when user is not a member of the requested team."""
    Team.objects.create(name="Other Team", slug="other-team")
    request = get_request_with_user("/?team=other-team", user_with_team)
    view_with_oauth2_data.request = request

    assert view_with_oauth2_data.requested_team is None


@pytest.mark.django_db()
def test_edit_application_queryset_scoped_to_user(request_factory, oauth_applications_for_multiple_users):
    """Test that EditApplication queryset is scoped to the logged-in user."""
    current_user = oauth_applications_for_multiple_users["current_user"]
    current_user_app = oauth_applications_for_multiple_users["current_user_app"]
    other_user_app = oauth_applications_for_multiple_users["other_user_app"]

    # Create request with current user
    request = request_factory.get("/oauth/applications/edit/")
    request.user = current_user

    # Create view instance and check queryset
    view = EditApplication()
    view.request = request
    queryset = view.get_queryset()

    # Verify only current user's application is in queryset
    assert current_user_app in queryset
    assert other_user_app not in queryset
    assert queryset.count() == 1


@pytest.mark.django_db()
def test_application_table_view_queryset_scoped_to_user(request_factory, oauth_applications_for_multiple_users):
    """Test that ApplicationTableView queryset is scoped to the logged-in user."""
    current_user = oauth_applications_for_multiple_users["current_user"]
    current_user_app = oauth_applications_for_multiple_users["current_user_app"]
    other_user_app = oauth_applications_for_multiple_users["other_user_app"]

    # Create request with current user
    request = request_factory.get("/oauth/applications/")
    request.user = current_user

    # Create view instance and check queryset
    view = ApplicationTableView()
    view.request = request
    queryset = view.get_queryset()

    # Verify only current user's application is in queryset
    assert current_user_app in queryset
    assert other_user_app not in queryset
    assert queryset.count() == 1
