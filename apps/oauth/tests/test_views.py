from unittest import mock

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.urls import reverse
from oauth2_provider.settings import oauth2_settings

from apps.oauth.models import OAuth2Application
from apps.oauth.views import TeamScopedAuthorizationView
from apps.teams.helpers import create_default_team_for_user
from apps.teams.models import Team
from apps.utils.factories.team import MembershipFactory, TeamWithUsersFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def request_factory():
    """Factory for creating HTTP requests."""
    return RequestFactory()


@pytest.fixture()
def user_with_team(db):
    """Create a user with a default team."""
    user = UserFactory.create()
    create_default_team_for_user(user, "User's Team")
    return user


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
def _oidc_signing_key():
    """The registration views ask for RS256, which `Application.clean()` rejects without a signing key."""
    with mock.patch.object(oauth2_settings, "OIDC_RSA_PRIVATE_KEY", "test-key", create=True):
        yield


def _create_application(team=None, user=None, name="App", **kwargs):
    return OAuth2Application.objects.create(
        name=name,
        team=team,
        user=user,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="https://example.com/callback",
        **kwargs,
    )


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
    MembershipFactory.create(team=other_team, user=user_with_team)

    request = get_request_with_user("/?team=other-team", user_with_team)
    view_with_oauth2_data.request = request

    initial = view_with_oauth2_data.get_initial()

    assert initial["team_slug"] == "other-team"


@pytest.mark.django_db()
def test_get_initial_ignores_team_parameter_for_team_scoped_application(
    get_request_with_user, user_with_team, view_with_oauth2_data
):
    """The application's own team wins over anything the request asks for."""
    application_team = Team.objects.create(name="App Team", slug="app-team")
    MembershipFactory.create(team=application_team, user=user_with_team)
    application = _create_application(team=application_team)
    user_team = user_with_team.teams.first()

    request = get_request_with_user(f"/?team={user_team.slug}&client_id={application.client_id}", user_with_team)
    view_with_oauth2_data.request = request

    assert view_with_oauth2_data.application_team == application_team
    assert view_with_oauth2_data.get_initial()["team_slug"] == application_team.slug


@pytest.mark.django_db()
def test_application_team_is_none_for_global_application(get_request_with_user, user_with_team, view_with_oauth2_data):
    application = _create_application(team=None)
    request = get_request_with_user(f"/?client_id={application.client_id}", user_with_team)
    view_with_oauth2_data.request = request

    assert view_with_oauth2_data.application_team is None


@pytest.mark.django_db()
def test_authorize_refuses_non_member_of_the_application_team(client, user_with_team):
    """A non-member can't authorize: the token would be scoped to a team they have no access to."""
    application_team = Team.objects.create(name="App Team", slug="app-team")
    application = _create_application(team=application_team)
    client.force_login(user_with_team)

    response = client.get(
        reverse("oauth_authorize"),
        {
            "client_id": application.client_id,
            "response_type": "code",
            "redirect_uri": "https://example.com/callback",
        },
    )

    assert response.status_code == 200
    assert response.context["error"]["error"] == "access_denied"
    assert "form" not in response.context


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
class TestTeamApplicationViews:
    """The application CRUD views are scoped to the team in the URL, not to the user who registered."""

    @pytest.fixture()
    def team(self):
        return TeamWithUsersFactory.create()

    @pytest.fixture()
    def admin_user(self, team):
        return team.members.first()

    @pytest.mark.parametrize("url_name", ["home", "new"])
    def test_pages_render(self, client, team, admin_user, url_name):
        client.force_login(admin_user)
        response = client.get(reverse(f"oauth_apps:{url_name}", args=[team.slug]))

        assert response.status_code == 200

    def test_table_lists_all_of_the_team_s_applications(self, client, team, admin_user):
        own = _create_application(team=team, user=admin_user, name="Own App")
        other_member = MembershipFactory.create(team=team).user
        colleagues = _create_application(team=team, user=other_member, name="Colleague's App")
        other_team = _create_application(team=TeamWithUsersFactory.create(), name="Other Team's App")
        global_app = _create_application(team=None, name="Global App")

        client.force_login(admin_user)
        response = client.get(reverse("oauth_apps:table", args=[team.slug]))

        assert response.status_code == 200
        listed = set(response.context["table"].data.data.values_list("id", flat=True))
        assert listed == {own.id, colleagues.id}
        assert other_team.id not in listed
        assert global_app.id not in listed

    @pytest.mark.usefixtures("_oidc_signing_key")
    def test_create_pins_the_application_to_the_team_in_the_url(self, client, team, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("oauth_apps:new", args=[team.slug]),
            {
                "name": "New App",
                "client_id": "new-client-id",
                "client_secret": "new-client-secret",
                "authorization_grant_type": OAuth2Application.GRANT_AUTHORIZATION_CODE,
                "redirect_uris": "https://example.com/callback",
                "algorithm": "RS256",
            },
        )

        assert response.status_code == 302
        # Registration is initiated from the team admin page, so it returns to that section.
        assert response.url == f"{reverse('single_team:manage_team', args=[team.slug])}#oauth-applications"
        application = OAuth2Application.objects.get(name="New App")
        assert application.team == team
        assert application.user == admin_user

    @pytest.mark.usefixtures("_oidc_signing_key")
    def test_create_registers_client_credentials_for_the_team(self, client, team, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("oauth_apps:new", args=[team.slug]),
            {
                "name": "Machine App",
                "client_id": "machine-client-id",
                "client_secret": "machine-client-secret",
                "authorization_grant_type": OAuth2Application.GRANT_CLIENT_CREDENTIALS,
                "algorithm": "RS256",
            },
        )

        assert response.status_code == 302
        assert OAuth2Application.objects.get(name="Machine App").team == team

    def test_edit_is_scoped_to_the_team_in_the_url(self, client, team, admin_user):
        """An application belonging to another team is not reachable, even by its own owner."""
        other_team = TeamWithUsersFactory.create()
        MembershipFactory.create(team=other_team, user=admin_user)
        application = _create_application(team=other_team, user=admin_user)

        client.force_login(admin_user)
        response = client.get(reverse("oauth_apps:edit", args=[team.slug, application.pk]))

        assert response.status_code == 404

    def test_edit_updates_a_team_application_registered_by_someone_else(self, client, team, admin_user):
        application = _create_application(team=team, user=MembershipFactory.create(team=team).user)

        client.force_login(admin_user)
        response = client.post(
            reverse("oauth_apps:edit", args=[team.slug, application.pk]),
            {
                "name": "Renamed",
                "client_id": application.client_id,
                "redirect_uris": "https://example.com/callback",
                "algorithm": "RS256",
            },
        )

        assert response.status_code == 302
        assert response.url == f"{reverse('single_team:manage_team', args=[team.slug])}#oauth-applications"
        application.refresh_from_db()
        assert application.name == "Renamed"
        assert application.team == team

    def test_delete_is_scoped_to_the_team_in_the_url(self, client, team, admin_user):
        other_team = TeamWithUsersFactory.create()
        MembershipFactory.create(team=other_team, user=admin_user)
        application = _create_application(team=other_team, user=admin_user)

        client.force_login(admin_user)
        response = client.delete(reverse("oauth_apps:delete", args=[team.slug, application.pk]))

        assert response.status_code == 404
        assert OAuth2Application.objects.filter(pk=application.pk).exists()

    def test_member_without_permission_cannot_register(self, client, team):
        membership = next(m for m in team.membership_set.all() if not m.has_perm("oauth.add_oauth2application"))

        client.force_login(membership.user)
        response = client.get(reverse("oauth_apps:new", args=[team.slug]))

        assert response.status_code == 403

    def test_non_member_cannot_list(self, client, team):
        client.force_login(UserFactory.create())
        response = client.get(reverse("oauth_apps:table", args=[team.slug]))

        assert response.status_code == 404


@pytest.mark.django_db()
class TestGlobalApplicationViews:
    """Global (team-less) applications are managed by superusers only."""

    @pytest.fixture()
    def superuser(self):
        return UserFactory.create(is_superuser=True, is_staff=True)

    @pytest.mark.parametrize("url_name", ["global_application_home", "global_application_new"])
    def test_pages_render_for_superusers(self, client, superuser, url_name):
        client.force_login(superuser)
        response = client.get(reverse(f"oauth2_provider:{url_name}"))

        assert response.status_code == 200

    def test_table_lists_only_global_applications(self, client, superuser):
        global_app = _create_application(team=None, name="Global App")
        team_app = _create_application(team=TeamWithUsersFactory.create(), name="Team App")

        client.force_login(superuser)
        response = client.get(reverse("oauth2_provider:global_application_table"))

        assert response.status_code == 200
        listed = set(response.context["table"].data.data.values_list("id", flat=True))
        assert listed == {global_app.id}
        assert team_app.id not in listed

    @pytest.mark.usefixtures("_oidc_signing_key")
    def test_create_registers_a_team_less_application(self, client, superuser):
        client.force_login(superuser)
        response = client.post(
            reverse("oauth2_provider:global_application_new"),
            {
                "name": "Global App",
                "client_id": "global-client-id",
                "client_secret": "global-client-secret",
                "authorization_grant_type": OAuth2Application.GRANT_AUTHORIZATION_CODE,
                "redirect_uris": "https://example.com/callback",
                "algorithm": "RS256",
            },
        )

        assert response.status_code == 302
        application = OAuth2Application.objects.get(name="Global App")
        assert application.team is None
        assert application.authorization_grant_type == OAuth2Application.GRANT_AUTHORIZATION_CODE

    @pytest.mark.usefixtures("_oidc_signing_key")
    def test_create_forces_the_authorization_code_grant(self, client, superuser):
        """A global client-credentials application would issue tokens scoped to no team at all."""
        client.force_login(superuser)
        response = client.post(
            reverse("oauth2_provider:global_application_new"),
            {
                "name": "Global Machine App",
                "client_id": "global-machine-client-id",
                "client_secret": "global-machine-client-secret",
                "authorization_grant_type": OAuth2Application.GRANT_CLIENT_CREDENTIALS,
                "redirect_uris": "https://example.com/callback",
                "algorithm": "RS256",
            },
        )

        assert response.status_code == 302
        application = OAuth2Application.objects.get(name="Global Machine App")
        assert application.authorization_grant_type == OAuth2Application.GRANT_AUTHORIZATION_CODE

    def test_edit_rejects_a_team_scoped_application(self, client, superuser):
        application = _create_application(team=TeamWithUsersFactory.create())

        client.force_login(superuser)
        response = client.get(reverse("oauth2_provider:global_application_edit", args=[application.pk]))

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "url_name",
        ["global_application_home", "global_application_table", "global_application_new"],
    )
    def test_non_superuser_has_no_access(self, client, url_name):
        """A non-superuser is not told the page exists."""
        team = TeamWithUsersFactory.create()
        client.force_login(team.members.first())

        response = client.get(reverse(f"oauth2_provider:{url_name}"))

        assert response.status_code == 404
