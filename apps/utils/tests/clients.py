from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.api.models import UserAPIKey
from apps.oauth.models import OAuth2AccessToken


class ApiTestClient(APIClient):
    def __init__(self, user, team, auth_method="api_key", read_only=False):
        super().__init__()
        self.user = user
        self.auth_method = auth_method
        if auth_method == "api_key":
            _user_key, self._token = UserAPIKey.objects.create_key(
                name=f"{user.email}-key", user=user, team=team, read_only=read_only
            )
        elif auth_method == "oauth":
            scope = " ".join([s for s in settings.OAUTH2_PROVIDER["SCOPES"]])
            access_token = OAuth2AccessToken.objects.create(
                user=user, team=team, expires=timezone.now() + timedelta(days=1), token="token", scope=scope
            )
            self._token = access_token.token

    def request(self, *args, **kwargs):
        if self.auth_method == "api_key":
            kwargs.setdefault(settings.API_KEY_CUSTOM_HEADER, self._token)
        elif self.auth_method == "oauth":
            kwargs.setdefault("HTTP_AUTHORIZATION", f"Bearer {self._token}")
        return super().request(*args, **kwargs)
