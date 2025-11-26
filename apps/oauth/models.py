"""
Custom OAuth2 models to extend the default django-oauth-toolkit models.

To avoid a myriad of migration issues, we have to implement all abstract models.
Related thread: https://github.com/django-oauth/django-oauth-toolkit/issues/634
"""

from django.db import models
from oauth2_provider.models import (
    AbstractAccessToken,
    AbstractApplication,
    AbstractGrant,
    AbstractIDToken,
    AbstractRefreshToken,
    ApplicationManager,
)

from apps.teams.models import Team


class OAuth2Application(AbstractApplication):
    # Custom application model can be extended here if needed
    objects = ApplicationManager()


class OAuth2AccessToken(AbstractAccessToken):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True)


class OAuth2Grant(AbstractGrant):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True)


class OAuth2IDToken(AbstractIDToken):
    pass


class OAuth2RefreshToken(AbstractRefreshToken):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True)
