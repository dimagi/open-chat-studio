from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed

from apps.oauth.models import OAuth2AccessToken, OAuth2Application
from apps.oauth.permissions import OAuth2AccessTokenAuthentication, is_client_credentials_request
from apps.teams.helpers import SyntheticTeamMembership
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def client_credentials_token(team):
    app = OAuth2Application.objects.create(
        name="machine-app",
        team=team,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
    )
    return OAuth2AccessToken.objects.create(
        application=app,
        team=team,
        token="machine-token",
        scope="sessions:read",
        expires=timezone.now() + timedelta(days=1),
    )


@pytest.mark.django_db()
def test_is_client_credentials_request(client_credentials_token):
    assert is_client_credentials_request(SimpleNamespace(auth=client_credentials_token)) is True


@pytest.mark.django_db()
def test_is_client_credentials_request_false_for_non_oauth():
    assert is_client_credentials_request(SimpleNamespace(auth=object())) is False
    assert is_client_credentials_request(SimpleNamespace(auth=None)) is False


@pytest.mark.django_db()
def test_authenticate_client_credentials_sets_synthetic_identity(rf, client_credentials_token, team):
    """A machine token yields an AnonymousUser + SyntheticTeamMembership, with no membership gate."""
    request = rf.get("/api/sessions/")
    request.META["HTTP_AUTHORIZATION"] = f"Bearer {client_credentials_token.token}"

    user, token = OAuth2AccessTokenAuthentication().authenticate(request)

    assert isinstance(user, AnonymousUser)
    assert request.team.id == team.id
    assert isinstance(request.team_membership, SyntheticTeamMembership)
    assert request.team_membership.is_team_admin() is False


@pytest.mark.django_db()
def test_authenticate_authorization_code_still_requires_membership(rf, team):
    """Regression: an authorization-code token for a user with no membership row is rejected."""
    non_member = TeamWithUsersFactory.create().members.first()
    app = OAuth2Application.objects.create(
        name="auth-code-app",
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="https://example.com/callback",
    )
    token = OAuth2AccessToken.objects.create(
        user=non_member,
        application=app,
        team=team,
        token="auth-code-token",
        scope="sessions:read",
        expires=timezone.now() + timedelta(days=1),
    )
    request = rf.get("/api/sessions/")
    request.META["HTTP_AUTHORIZATION"] = f"Bearer {token.token}"

    with pytest.raises(AuthenticationFailed):
        OAuth2AccessTokenAuthentication().authenticate(request)
