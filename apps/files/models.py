import contextlib
import mimetypes
import pathlib
from datetime import datetime

import magic
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.urls import reverse
from pgvector.django import HalfVectorField

from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, VersionsObjectManagerMixin
from apps.generics.chips import Chip
from apps.teams.models import BaseTeamModel
from apps.teams.utils import get_slug_for_team
from apps.utils.conversions import bytes_to_megabytes, humanize_bytes
from apps.utils.deletion import get_related_m2m_objects
from apps.utils.fields import SanitizedJSONField
from apps.web.meta import absolute_url


class FilePurpose(models.TextChoices):
    ASSISTANT = "assistant", "Assistant"
    COLLECTION = "collection", "Collection"
    EVALUATION_DATASET = "evaluation_dataset", "Evaluation Dataset"
    DATA_EXPORT = "data_export", "Data Export"
    MESSAGE_MEDIA = "message_media", "Message Media"


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
    metadata = SanitizedJSONField(default=dict)

    objects = FileObjectManager()

    def __str__(self) -> str:
        return f"{self.name}"

    @classmethod
    def from_external_source(
        cls, filename, external_file, external_id, external_source, team_id, metadata: dict = None
    ):
        if existing := File.objects.filter(
            external_id=external_id, external_source=external_source, team_id=team_id
        ).first():
            return existing

        return cls.create(filename, external_file, team_id, external_id, external_source, metadata)

    @classmethod
    def create(
        cls,
        filename: str,
        file_obj,
        team_id: int,
        external_id: str = "",
        external_source: str = "",
        metadata: dict | None = None,
        purpose: FilePurpose | None = None,
        expiry_date: datetime | None = None,
    ):
        content = file_obj.read() if file_obj else None

        content_type = mimetypes.guess_type(filename)[0]
        if not content_type and content:
            # typically means the filename doesn't have an extension
            content_type = magic.from_buffer(content, mime=True)
            extension = mimetypes.guess_extension(content_type)
            # leading '.' is included
            filename = f"{filename}{extension}"

        new_file = File(
            name=filename,
            external_id=external_id,
            external_source=external_source,
            team_id=team_id,
            content_type=content_type,
            metadata=metadata or {},
            expiry_date=expiry_date,
        )

        if purpose:
            new_file.purpose = purpose

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
    def is_image(self):
        return self.content_type.startswith("image/")

    @property
    def display_size(self):
        return humanize_bytes(self.content_size)

    @property
    def size_mb(self) -> float:
        """Returns the size of this file in megabytes"""
        return bytes_to_megabytes(self.content_size)

    def _get_version_details(self) -> VersionDetails:
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
        self._clear_version_cache()
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
        return absolute_url(
            reverse("experiments:download_file", args=[get_slug_for_team(self.team_id), experiment_session_id, self.id])
        )

    def get_citation_url(self, experiment_session_id: int) -> str:
        if citation_url := self.metadata.get("citation_url"):
            return citation_url
        return absolute_url(
            reverse("experiments:download_file", args=[get_slug_for_team(self.team_id), experiment_session_id, self.id])
        )

    @property
    def citation_text(self):
        if citation_text := self.metadata.get("citation_text"):
            return citation_text
        return self.name

    def get_absolute_url(self):
        return reverse("files:file_edit", args=[get_slug_for_team(self.team_id), self.id])

    def as_chip(self) -> Chip:
        label = self.name
        return Chip(label=label, url=self.get_absolute_url())

    def delete_or_archive(self):
        """Deletes the file if it has no versions, otherwise archives it."""
        if self.has_versions:
            self.archive()
        else:
            self.delete()

    def read_content(self) -> str:
        from apps.documents.readers import Document

        document = Document.from_file(self)
        return document.get_contents_as_string()

    def is_used(self) -> bool:
        # get_related_m2m_objects returns a dictionary with the file instance as the key if there are related objects
        return self in get_related_m2m_objects([self])


class FileChunkEmbeddingObjectManager(VersionsObjectManagerMixin):
    pass


class FileChunkEmbedding(BaseTeamModel, VersionsMixin):
    # See 0009_remove_filechunkembedding_embedding_index_and_more.py migration for the index
    file = models.ForeignKey(File, on_delete=models.CASCADE)
    collection = models.ForeignKey("documents.Collection", on_delete=models.CASCADE)
    chunk_number = models.PositiveIntegerField()
    text = models.TextField()
    page_number = models.PositiveIntegerField(blank=True)
    embedding = HalfVectorField(dimensions=settings.EMBEDDING_VECTOR_SIZE)
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)

    objects = FileChunkEmbeddingObjectManager()
