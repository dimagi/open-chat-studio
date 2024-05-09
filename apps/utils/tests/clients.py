from django.conf import settings
from rest_framework.test import APIClient

from apps.api.models import UserAPIKey


class ApiTestClient(APIClient):
    def __init__(self, user, team):
        super().__init__()
        _user_key, self._api_key = UserAPIKey.objects.create_key(name=f"{user.email}-key", user=user, team=team)

    def request(self, *args, **kwargs):
        kwargs.setdefault(settings.API_KEY_CUSTOM_HEADER, self._api_key)
        return super().request(*args, **kwargs)
