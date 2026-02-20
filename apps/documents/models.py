from collections.abc import Iterator

from django.db import models, transaction
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django_pydantic_field import SchemaField
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.documents.datamodels import ChunkingStrategy, CollectionFileMetadata, DocumentSourceConfig
from apps.documents.exceptions import IndexConfigurationException
from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, VersionsObjectManagerMixin
from apps.service_providers.models import EmbeddingProviderModel
from apps.teams.models import BaseTeamModel
from apps.teams.utils import get_slug_for_team
from apps.utils.conversions import bytes_to_megabytes
from apps.utils.deletion import (
    get_related_pipeline_experiments_queryset,
    get_related_pipeline_experiments_queryset_list_param,
    get_related_pipelines_queryset,
    get_related_pipelines_queryset_for_list_param,
)


class CollectionObjectManager(VersionsObjectManagerMixin, AuditingManager):
    pass


class FileStatus(models.TextChoices):
    # See https://platform.openai.com/docs/api-reference/vector-stores-files/file-object
    PENDING = ("pending", _("Pending"))
    IN_PROGRESS = ("in_progress", _("In Progress"))
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")


class CollectionFileQuerySet(models.QuerySet):
    def is_manually_uploaded(self):
        return self.filter(document_source__isnull=True)


class CollectionFileManager(models.Manager):
    def get_queryset(self):
        return CollectionFileQuerySet(self.model, using=self._db)

    def is_manually_uploaded(self):
        return self.get_queryset().is_manually_uploaded()


class CollectionFile(models.Model):
    file = models.ForeignKey("files.File", on_delete=models.CASCADE)
    collection = models.ForeignKey("documents.Collection", on_delete=models.CASCADE)
    document_source = models.ForeignKey("documents.DocumentSource", on_delete=models.CASCADE, null=True)
    status = models.CharField(max_length=64, choices=FileStatus.choices, blank=True)
    metadata = SchemaField(schema=CollectionFileMetadata, null=True)
    external_id = models.CharField(max_length=255, blank=True, help_text="ID of file in document source")

    objects = CollectionFileManager()

    def __str__(self) -> str:
        return f"{self.file.name} in {self.collection.name}"

    @property
    def file_size_mb(self):
        return self.file.size_mb

    @property
    def chunking_strategy(self) -> ChunkingStrategy | None:
        if self.metadata:
            return self.metadata.chunking_strategy

    @property
    def status_enum(self):
        return FileStatus(self.status)


@audit_fields(
    "name",
    "version_number",
    "llm_provider",
    "is_index",
    "is_remote_index",
    "embedding_provider_model",
    "openai_vector_store_id",
    audit_special_queryset_writes=True,
)
class Collection(BaseTeamModel, VersionsMixin):
    name = models.CharField(max_length=255)
    summary = models.TextField(
        blank=True,
        help_text="Description of this collection used to with additional context to LLMs to allow "
        "them to make decisions about when to search this collection.",
    )
    files = models.ManyToManyField("files.File", blank=False, through=CollectionFile, related_name="collections")
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    version_number = models.PositiveIntegerField(default=1)
    llm_provider = models.ForeignKey(
        "service_providers.LlmProvider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="LLM Provider",
    )
    embedding_provider_model = models.ForeignKey(
        EmbeddingProviderModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="collection_embedding_model",
    )
    is_remote_index = models.BooleanField(
        default=False, help_text="If selected, this index will be created at and hosted by the selected provider"
    )
    openai_vector_store_id = models.CharField(blank=True, max_length=255)
    is_index = models.BooleanField(default=False)

    objects = CollectionObjectManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "name", "version_number", "working_version_id"],
                name="unique_collection_version_per_team",
            )
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def index_name(self) -> str:
        name = f"collection-{get_slug_for_team(self.team_id)}-{slugify(self.name)}-{self.id}"  # noqa: F821
        if self.is_a_version:
            return f"{name} v{self.version_number}"
        return name

    @property
    def size(self) -> float:
        """Returns the size of this collection in megabytes"""
        bytes = self.files.aggregate(bytes=models.Sum("content_size"))["bytes"] or 0
        return bytes_to_megabytes(bytes)

    def _get_version_details(self) -> VersionDetails:
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(group_name="General", name="name", raw_value=self.name),
                VersionField(group_name="General", name="summary", raw_value=self.summary),
                VersionField(group_name="General", name="llm_provider", raw_value=self.llm_provider),
                VersionField(
                    group_name="General", name="embedding_provider_model", raw_value=self.embedding_provider_model
                ),
                VersionField(
                    group_name="General",
                    name="files",
                    queryset=self.files.filter(collectionfile__document_source=None).all(),
                ),
                VersionField(
                    group_name="Document Sources",
                    name="document_sources",
                    queryset=self.document_sources.all(),
                ),
            ],
        )

    @transaction.atomic()
    def create_new_version(self, save=True):  # ty: ignore[invalid-method-override]
        """
        When a collection is indexed, we need to create a new vector store when we create a new version of it
        and upload the file versions to it.
        """
        from apps.documents.tasks import index_collection_files

        version_number = self.version_number
        self.version_number = version_number + 1
        self.save(update_fields=["version_number"])

        new_version = super().create_new_version(save=False)
        new_version.version_number = version_number
        new_version.openai_vector_store_id = ""
        new_version.save()

        file_versions: dict[int, int] = {}

        def _version_files(file_queryset, new_object_version, through_defaults: dict = None):
            nonlocal file_versions
            _file_versions: dict[int, int] = {}
            for file in file_queryset.iterator(chunk_size=50):
                file_version = file.create_new_version(save=False)
                file_version.external_id = ""
                file_version.external_source = ""
                file_version.save()
                _file_versions[file.id] = file_version.id
            new_object_version.files.add(*list(_file_versions.values()), through_defaults=through_defaults)
            file_versions = file_versions | _file_versions

        _version_files(self.files.filter(collectionfile__document_source=None), new_version)

        for document_source in self.document_sources.all():
            doc_source_version = document_source.create_new_version(save=False)
            doc_source_version.collection = new_version
            doc_source_version.save()
            _version_files(
                document_source.files,
                doc_source_version,
                through_defaults={
                    "collection": new_version,
                },
            )

        if self.is_index:
            # Create a new vector store at llm service for the new version of the collection.
            # Optimization suggestion: Only when the file set changed, should we create a new vector store at the
            # provider
            if self.is_remote_index:
                new_version.ensure_remote_index_created()

                # Upload files to vector store
                if collection_files := CollectionFile.objects.filter(collection_id=new_version.id):
                    index_collection_files(collection_files)
            else:
                # Create versions of file chunk embeddings and add them to the new collection
                for embedding in self.filechunkembedding_set.iterator(chunk_size=50):
                    # Skip embeddings for files that are no longer in the collection
                    if embedding.file_id not in file_versions:
                        continue

                    embedding_version = embedding.create_new_version(save=False)
                    embedding_version.collection = new_version

                    file_version_id = file_versions[embedding.file_id]
                    embedding_version.file_id = file_version_id
                    embedding_version.save()

        return new_version

    def get_absolute_url(self):
        return reverse("documents:single_collection_home", args=[get_slug_for_team(self.team_id), self.id])

    def get_related_nodes_queryset(self) -> models.QuerySet:
        index_references = get_related_pipelines_queryset_for_list_param(self, "collection_index_ids").distinct()
        collection_references = get_related_pipelines_queryset(self, "collection_id").distinct()
        return index_references | collection_references

    def get_related_experiments_queryset(self) -> models.QuerySet:
        """
        Get all experiments that reference this collection through a pipeline. This includes both published and working
        experiments. When check_versions is True, it will return all experiments that reference any version of this
        collection.
        """
        # TODO: Update assistant archive code to use get_related_pipeline_experiments_queryset
        ids = list(self.versions.values_list("id", flat=True)) + [self.id]

        index_references = get_related_pipeline_experiments_queryset_list_param(ids, "collection_index_ids").filter(
            models.Q(is_default_version=True) | models.Q(working_version__id__isnull=True),
        )
        collection_references = get_related_pipeline_experiments_queryset(ids, "collection_id").filter(
            models.Q(is_default_version=True) | models.Q(working_version__id__isnull=True),
        )
        return index_references | collection_references

    @transaction.atomic()
    def archive(self):
        """
        Archive the collection with its files and remove the index and the files at the remote service, if it has one
        """
        from apps.documents.tasks import delete_collection_task

        if self.get_related_nodes_queryset().exists():
            return False

        if self.is_working_version and self.get_related_experiments_queryset().exists():
            return False

        super().archive()
        for doc_source in self.document_sources.all():
            doc_source.archive(delete_files=False)

        delete_collection_task.delay(self.id)
        return True

    def has_failed_index_uploads(self) -> bool:
        """
        Check if any of the files in this collection failed to upload to an index
        """
        return CollectionFile.objects.filter(
            collection=self,
            status=FileStatus.FAILED,
        ).exists()

    def has_pending_index_uploads(self) -> bool:
        """
        Check if any of the files in this collection are not yet uploaded to an index
        """
        return CollectionFile.objects.filter(
            collection=self,
            status__in=[FileStatus.PENDING, FileStatus.IN_PROGRESS],
        ).exists()

    def remove_remote_index(self):
        """Remove the index backend"""
        manager = self.get_index_manager()
        manager.delete_remote_index()
        self.openai_vector_store_id = ""
        self.save(update_fields=["openai_vector_store_id"])

    def get_index_manager(self):
        if self.is_index and self.is_remote_index:
            return self.llm_provider.get_remote_index_manager(self.openai_vector_store_id)
        else:
            return self.llm_provider.get_local_index_manager(embedding_model_name=self.embedding_provider_model.name)

    def get_query_vector(self, query: str) -> list[float]:
        """Get the embedding vector for a query using the embedding provider model"""
        if not self.embedding_provider_model:
            raise IndexConfigurationException("Embedding provider model is missing this collection")

        index_manager = self.get_index_manager()
        return index_manager.get_embedding_vector(query)

    def add_files_to_index(
        self,
        collection_files: Iterator[CollectionFile],
        chunk_size: int = None,
        chunk_overlap: int = None,
    ):
        index_manager = self.get_index_manager()
        index_manager.add_files(collection_files, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    def ensure_remote_index_created(self, file_ids: list[str] = None):
        """
        Ensure that the remote index is created for this collection if it is not already created.
        This is used when the collection is created or when the version is created.
        """
        if not self.is_remote_index or self.openai_vector_store_id:
            return

        self.openai_vector_store_id = self.llm_provider.create_remote_index(name=self.index_name, file_ids=file_ids)
        self.save(update_fields=["openai_vector_store_id"])


class SourceType(models.TextChoices):
    GITHUB = "github", _("GitHub Repository")
    CONFLUENCE = "confluence", _("Confluence")

    @property
    def css_logo(self):
        return {
            SourceType.GITHUB: "fa-brands fa-github",
            SourceType.CONFLUENCE: "fa-brands fa-confluence",
        }[self]


class SyncStatus(models.TextChoices):
    SUCCESS = "success", _("Success")
    FAILED = "failed", _("Failed")
    IN_PROGRESS = "in_progress", _("In Progress")


class DocumentSourceManager(VersionsObjectManagerMixin, AuditingManager):
    pass


@audit_fields(
    "collection",
    "source_type",
    "config",
    "auto_sync_enabled",
    "auth_provider",
    audit_special_queryset_writes=True,
)
class DocumentSource(BaseTeamModel, VersionsMixin):
    collection = models.ForeignKey(
        Collection,
        on_delete=models.CASCADE,
        related_name="document_sources",
        help_text="The collection this document source belongs to",
    )
    source_type = models.CharField(max_length=20, choices=SourceType.choices, help_text="Type of document source")
    config = SchemaField(schema=DocumentSourceConfig, help_text="Configuration for the document source")
    auto_sync_enabled = models.BooleanField(default=False, help_text="Automatically sync this source on a schedule")
    last_sync = models.DateTimeField(null=True, blank=True, help_text="Timestamp of the last successful sync")
    files = models.ManyToManyField("files.File", blank=False, through=CollectionFile, related_name="document_sources")
    sync_task_id = models.CharField(
        max_length=40, blank=True, default="", help_text="System ID of the sync task, if present."
    )
    auth_provider = models.ForeignKey("service_providers.AuthProvider", on_delete=models.PROTECT, blank=True, null=True)
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)

    objects = DocumentSourceManager()

    def __str__(self) -> str:
        return f"{self.get_source_type_display()}: {self.source_config}"

    @property
    def source_type_enum(self):
        return SourceType(self.source_type)

    @property
    def source_config(self):
        """Get the configuration for the specific source type"""
        if self.source_type == SourceType.GITHUB:
            return self.config.github
        elif self.source_type == SourceType.CONFLUENCE:
            return self.config.confluence
        return None

    def _get_version_details(self) -> VersionDetails:
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(name="source_type", raw_value=self.get_source_type_display()),
                VersionField(name="config", raw_value=self.source_config),
                VersionField(name="files", queryset=self.files.all()),
            ],
        )

    def archive(self, delete_files=True):
        from apps.documents.tasks import delete_document_source_task

        super().archive()
        if delete_files:
            delete_document_source_task.delay(self.id)

    def has_sync_errors(self) -> bool:
        """Check if the last sync had errors"""
        last_log = self.sync_logs.first()
        return last_log is not None and last_log.status == SyncStatus.FAILED


class DocumentSourceSyncLog(models.Model):
    document_source = models.ForeignKey(
        DocumentSource,
        on_delete=models.CASCADE,
        related_name="sync_logs",
        help_text="The document source this sync log belongs to",
    )
    sync_date = models.DateTimeField(auto_now_add=True, help_text="When the sync was performed")
    status = models.CharField(max_length=20, choices=SyncStatus.choices, help_text="Status of the sync")
    files_added = models.IntegerField(default=0, help_text="Number of files added during sync")
    files_updated = models.IntegerField(default=0, help_text="Number of files updated during sync")
    files_removed = models.IntegerField(default=0, help_text="Number of files removed during sync")
    error_message = models.TextField(blank=True, help_text="Error message if sync failed")
    duration_seconds = models.FloatField(null=True, blank=True, help_text="Duration of the sync in seconds")

    class Meta:
        ordering = ["-sync_date"]

    def __str__(self) -> str:
        return f"Sync of {self.document_source} on {self.sync_date}"

    @property
    def total_files_processed(self) -> int:
        return self.files_added + self.files_updated + self.files_removed
