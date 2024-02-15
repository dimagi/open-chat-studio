import mimetypes
import pathlib

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
            filename = self.file.name
            try:
                filename = pathlib.Path(filename).name
            except Exception:
                pass

            self.content_type = mimetypes.guess_type(filename)[0]
            if not self.name:
                self.name = filename
        super().save(*args, **kwargs)
