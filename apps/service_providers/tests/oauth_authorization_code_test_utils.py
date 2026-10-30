from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.urls import reverse

from apps.service_providers.models import AuthProviderType
from apps.utils.factories.service_provider_factories import AuthProviderFactory

CONFIG = {
    "client_id": "client",
    "client_secret": "secret",
    "authorize_url": "https://auth.example.test/authorize",
    "token_url": "https://auth.example.test/token",
    "scope": "read write",
    "token_endpoint_auth_method": "client_secret_basic",
}


def make_provider(team, **overrides):
    return AuthProviderFactory.create(
        team=team, type=AuthProviderType.oauth_authorization_code, config={**CONFIG, **overrides}
    )


def connect(client, team, provider):
    client.force_login(team.members.first())
    url = reverse("service_providers:oauth_connect", kwargs={"team_slug": team.slug, "pk": provider.pk})
    with patch("apps.service_providers.oauth_views.validate_token_url"):
        response = client.get(url)
    query = parse_qs(urlparse(response["Location"]).query)
    return query["state"][0]
