import mimetypes
import pathlib

import magic
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
    expiry_date = models.DateTimeField(null=True)

    @classmethod
    def from_external_source(cls, filename, external_file, external_id, external_source, team_id):
        if existing := File.objects.filter(
            external_id=external_id, external_source=external_source, team_id=team_id
        ).first():
            return existing

        file_content_bytes = external_file.read() if external_file else None

        content_type = mimetypes.guess_type(filename)[0]
        if not content_type and external_file:
            # typically means the filename doesn't have an extension
            content_type = magic.from_buffer(file_content_bytes, mime=True)
            extension = mimetypes.guess_extension(content_type)
            # leading '.' is included
            filename = f"{filename}{extension}"

        return cls.from_content(filename, file_content_bytes, content_type, team_id, external_id, external_source)

    @classmethod
    def from_content(cls, filename, content, content_type, team_id, external_id="", external_source=""):
        new_file = File(
            name=filename,
            external_id=external_id,
            external_source=external_source,
            team_id=team_id,
            content_type=content_type,
        )

        if content:
            content_file = ContentFile(content, name=filename)
            new_file.file = content_file
            new_file.size = content_file.size

        new_file.save()
        return new_file

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
