"""
Custom OAuth2 models to extend the default django-oauth-toolkit models.

To avoid a myriad of migration issues, we have to implement all abstract models.
Related thread: https://github.com/django-oauth/django-oauth-toolkit/issues/634
"""

from django.db import models
from django.urls import reverse
from oauth2_provider.models import (
    AbstractAccessToken,
    AbstractApplication,
    AbstractGrant,
    AbstractIDToken,
    AbstractRefreshToken,
    ApplicationManager,
)

from apps.generics.chips import Chip
from apps.teams.models import Team
from apps.teams.utils import get_slug_for_team


class OAuth2Application(AbstractApplication):
    # The team is pinned here at registration and every token the application issues is scoped to it.
    # Null means the application is *global*: only superusers may register those (from the site admin
    # area), and only with the authorization-code grant, where the team is instead chosen by the
    # authorizing user and threaded via the Grant.
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True)

    objects = ApplicationManager()

    def get_absolute_url(self):
        if self.team_id:
            return reverse("oauth_apps:edit", args=[get_slug_for_team(self.team_id), self.pk])
        return reverse("oauth2_provider:global_application_edit", args=[self.pk])

    def as_chip(self) -> Chip:
        return Chip(label=self.name, url=self.get_absolute_url())


class OAuth2AccessToken(AbstractAccessToken):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True)


class OAuth2Grant(AbstractGrant):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True)


class OAuth2IDToken(AbstractIDToken):
    pass


class OAuth2RefreshToken(AbstractRefreshToken):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True)
