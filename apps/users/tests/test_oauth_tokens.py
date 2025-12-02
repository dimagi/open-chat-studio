from datetime import timedelta

import pytest
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
