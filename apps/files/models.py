import mimetypes
import pathlib

from django.core.files.base import ContentFile
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

    @staticmethod
    def get_content_type(file):
        filename = file.name
        try:
            filename = pathlib.Path(filename).name
        except Exception:
            pass
        try:
            return mimetypes.guess_type(filename)[0] or "application/octet-stream"
        except Exception:
            return "application/octet-stream"

    def save(self, *args, **kwargs):
        if self.file:
            self.content_size = self.file.size
            filename = self.file.name
            if not self.name:
                self.name = filename
            if not self.content_type:
                self.content_type = File.get_content_type(self.file)
        super().save(*args, **kwargs)

    def duplicate(self):
        new_file = File(
            name=self.name,
            external_source=self.external_source,
            external_id=self.external_id,
            content_size=self.content_size,
            content_type=self.content_type,
            schema=self.schema,
            team=self.team,
        )
        if self.file and self.file.storage.exists(self.file.name):
            new_file_file = ContentFile(self.file.read())
            new_file_file.name = self.file.name
            new_file.file = new_file_file
        new_file.save()
        return new_file
