from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse
from django.utils import timezone

from apps.oauth.models import OAuth2AccessToken, OAuth2Application
from apps.oauth.validator import APIScopedValidator
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def client_credentials_app(team):
    """A client-credentials application pinned to a team, with a known plaintext secret."""
    app = OAuth2Application(
        name="machine-app",
        client_id="machine-client-id",
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
        team=team,
        hash_client_secret=False,
    )
    app.client_secret = "machine-client-secret"
    app.save()
    return app


@pytest.mark.django_db()
def test_token_endpoint_issues_team_scoped_token(client, client_credentials_app, team):
    """POST /o/token/ with grant_type=client_credentials returns a token pinned to the app's team."""
    response = client.post(
        reverse("oauth2_provider:token"),
        {
            "grant_type": "client_credentials",
            "client_id": client_credentials_app.client_id,
            "client_secret": "machine-client-secret",
            "scope": "sessions:read",
        },
    )

    assert response.status_code == 200, response.content
    token = OAuth2AccessToken.objects.get(token=response.json()["access_token"])
    assert token.team_id == team.id
    assert token.user is None


@pytest.mark.django_db()
def test_additional_claims_omit_user_for_machine_token(client_credentials_app, team):
    """get_additional_claims must not read user attributes when there is no user (machine token)."""
    validator = APIScopedValidator()
    access_token = OAuth2AccessToken.objects.create(
        application=client_credentials_app,
        team=team,
        token="machine-token",
        scope="sessions:read",
        expires=timezone.now() + timedelta(days=1),
    )
    request = SimpleNamespace(user=AnonymousUser(), access_token=access_token)

    claims = validator.get_additional_claims(request)

    assert "sub" not in claims
    assert claims["team"] == team.slug


@pytest.mark.django_db()
def test_validate_scopes_restricts_client_credentials_to_allow_list(settings):
    """A client-credentials app can only be granted scopes on the allow-list."""
    settings.OAUTH_CLIENT_CREDENTIALS_SCOPES = ["sessions:read"]
    validator = APIScopedValidator()
    client = OAuth2Application(authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS)

    # sessions:read and chatbots:read are both available scopes, but only sessions:read is allow-listed.
    assert validator.validate_scopes("cid", ["sessions:read"], client, None) is True
    assert validator.validate_scopes("cid", ["chatbots:read"], client, None) is False


@pytest.mark.django_db()
def test_validate_scopes_does_not_restrict_authorization_code(settings):
    """The allow-list applies only to client-credentials; authorization-code is unaffected."""
    settings.OAUTH_CLIENT_CREDENTIALS_SCOPES = ["sessions:read"]
    validator = APIScopedValidator()
    client = OAuth2Application(authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE)

    assert validator.validate_scopes("cid", ["chatbots:read"], client, None) is True
