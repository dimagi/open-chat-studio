from django.conf import settings
from django.db import models
from rest_framework_api_key.models import AbstractAPIKey

from apps.teams.models import Team


class UserAPIKey(AbstractAPIKey):
    """
    API Key associated with a User, allowing you to scope the key's API access based on what the user
    is allowed to view/do.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="api_keys")
    read_only = models.BooleanField(default=True)

    class Meta(AbstractAPIKey.Meta):
        verbose_name = "User API key"
        verbose_name_plural = "User API keys"
