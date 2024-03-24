from django.db import models

from apps.teams.models import BaseTeamModel


class Pipeline(BaseTeamModel):
    name = models.CharField(max_length=128)
    data = models.JSONField()

    class Meta:
        ordering = ["-created_at"]
