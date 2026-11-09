from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.oauth.models import OAuth2AccessToken, OAuth2Application
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def oauth_app(db):
    return OAuth2Application.objects.create(
        name="Test OAuth App",
        client_type="confidential",
        authorization_grant_type="authorization-code",
        redirect_uris="http://localhost:8000/callback",
    )


@pytest.fixture()
def oauth_token(team_with_users, oauth_app, db):
    user = team_with_users.members.first()
    return OAuth2AccessToken.objects.create(
        user=user,
        team=team_with_users,
        application=oauth_app,
        token="test-token-12345",
        expires=timezone.now() + timedelta(days=30),
        scope="chatbots:read sessions:read",
    )


@pytest.mark.django_db()
class TestOAuthTokenViews:
    def test_revoke_oauth_token(self, client, team_with_users, oauth_token):
        """Test revoking an OAuth token"""
        user = team_with_users.members.first()
        client.force_login(user)

        token_id = oauth_token.id

        response = client.post(
            reverse("users:revoke_oauth_token"),
            {"token_id": token_id},
        )

        assert response.status_code == 302
        assert response.url == reverse("users:user_profile")

        # Verify token was deleted
        assert not OAuth2AccessToken.objects.filter(id=token_id).exists()


def _team_lookup_queries(captured):
    return [q for q in captured.captured_queries if q["sql"].lstrip().startswith('SELECT "teams_team"."id"')]


@pytest.mark.django_db()
def test_profile_oauth_tokens_team_does_not_cause_n_plus_one(client, team_with_users, oauth_app):
    """Rendering the profile page should not issue a team query per OAuth token."""
    user = team_with_users.members.first()
    client.force_login(user)

    def _create_token(i):
        OAuth2AccessToken.objects.create(
            user=user,
            team=team_with_users,
            application=oauth_app,
            token=f"n-plus-one-token-{i}",
            expires=timezone.now() + timedelta(days=30),
            scope="chatbots:read",
        )

    # Warm up: the first request creates notification preferences and populates caches,
    # which would otherwise skew the query counts below.
    assert client.get(reverse("users:user_profile")).status_code == 200

    _create_token(0)
    with CaptureQueriesContext(connection) as single:
        response = client.get(reverse("users:user_profile"))
    assert response.status_code == 200

    for i in range(1, 5):
        _create_token(i)
    with CaptureQueriesContext(connection) as many:
        response = client.get(reverse("users:user_profile"))
    assert response.status_code == 200

    assert len(_team_lookup_queries(many)) == len(_team_lookup_queries(single))
