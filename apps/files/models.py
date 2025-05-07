import contextlib
import mimetypes
import pathlib

import magic
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.urls import reverse

from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, VersionsObjectManagerMixin
from apps.generics.chips import Chip
from apps.teams.models import BaseTeamModel
from apps.utils.conversions import bytes_to_megabytes
from apps.web.meta import absolute_url


class FilePurpose(models.TextChoices):
    ASSISTANT = "assistant", "Assistant"
    COLLECTION = "collection", "Collection"


class FileObjectManager(VersionsObjectManagerMixin, models.Manager):
    pass


class File(BaseTeamModel, VersionsMixin):
    name = models.CharField(max_length=255)
    file = models.FileField()
    external_source = models.CharField(max_length=255, blank=True)
    external_id = models.CharField(max_length=255, blank=True)
    content_size = models.PositiveIntegerField(null=True, blank=True)
    content_type = models.CharField(blank=True)
    expiry_date = models.DateTimeField(null=True)
    summary = models.TextField(max_length=settings.MAX_SUMMARY_LENGTH, blank=True)  # This is roughly 1 short paragraph
    purpose = models.CharField(max_length=255, choices=FilePurpose.choices)
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict)

    objects = FileObjectManager()

    def __str__(self) -> str:
        return f"{self.name}"

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
            new_file.content_size = content_file.size

        new_file.save()
        return new_file

    @staticmethod
    def get_content_type(file):
        filename = file.name
        with contextlib.suppress(Exception):
            filename = pathlib.Path(filename).name
        try:
            return mimetypes.guess_type(filename)[0] or "application/octet-stream"
        except Exception:
            return "application/octet-stream"

    @property
    def size_mb(self) -> float:
        """Returns the size of this file in megabytes"""
        return bytes_to_megabytes(self.content_size)

    @property
    def version_details(self) -> VersionDetails:
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(group_name="General", name="name", raw_value=self.name),
                VersionField(group_name="General", name="summary", raw_value=self.summary),
            ],
        )

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
            metadata=self.metadata,
            team=self.team,
        )
        if self.file and self.file.storage.exists(self.file.name):
            new_file_file = ContentFile(self.file.read())
            new_file_file.name = self.file.name
            new_file.file = new_file_file
        new_file.save()
        return new_file

    def get_collection_references(self):
        return self.collections.all()

    def download_link(self, experiment_session_id: int) -> str:
        return absolute_url(reverse("experiments:download_file", args=[self.team.slug, experiment_session_id, self.id]))

    def get_absolute_url(self):
        return reverse("files:file_edit", args=[self.team.slug, self.id])

    def as_chip(self) -> Chip:
        label = self.name
        return Chip(label=label, url=self.get_absolute_url())

    def delete_or_archive(self):
        """Deletes the file if it has no versions, otherwise archives it."""
        if self.has_versions:
            self.archive()
        else:
            self.delete()
