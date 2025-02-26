from django.db import models

from apps.teams.models import BaseTeamModel


class RepositoryType(models.TextChoices):
    COLLECTION = "collection", "Collection"


class Repository(BaseTeamModel):
    name = models.CharField(max_length=255)
    summary = models.TextField()
    type = models.CharField(choices=RepositoryType.choices, default=RepositoryType.COLLECTION)
    files = models.ManyToManyField("files.File", blank=False)
