from django.db import models

from apps.teams.models import BaseTeamModel


class File(BaseTeamModel):
    name = models.CharField(max_length=255)
    file = models.FileField()
    external_source = models.CharField(max_length=255, blank=True)
    external_id = models.CharField(max_length=255, blank=True)
    content_size = models.PositiveIntegerField(null=True, blank=True)
    content_type = models.CharField(blank=True)
    schema = models.JSONField(default=dict, blank=True)

    def save(self, *args, **kwargs):
        if self.file:
            self.content_size = self.file.size
        super().save(*args, **kwargs)
