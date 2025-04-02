from django.db import models, transaction
from django.urls import reverse
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, VersionsObjectManagerMixin
from apps.teams.models import BaseTeamModel
from apps.utils.conversions import bytes_to_megabytes
from apps.utils.deletion import get_related_pipelines_queryset


class CollectionObjectManager(VersionsObjectManagerMixin, AuditingManager):
    pass


@audit_fields(
    "name",
    "files",
    audit_special_queryset_writes=True,
)
class Collection(BaseTeamModel, VersionsMixin):
    name = models.CharField(max_length=255)
    files = models.ManyToManyField("files.File", blank=False)
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    objects = CollectionObjectManager()
    version_number = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "name", "version_number"], name="unique_collection_version_per_team"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name}"

    @property
    def size(self) -> float:
        """Returns the size of this collection in megabytes"""
        bytes = self.files.aggregate(bytes=models.Sum("content_size"))["bytes"] or 0
        return bytes_to_megabytes(bytes)

    @property
    def file_names(self) -> list[str]:
        return list(self.files.values_list("name", flat=True))

    def get_node_references(self) -> models.QuerySet:
        return get_related_pipelines_queryset(self, "collection_id").distinct()

    @property
    def version_details(self) -> VersionDetails:
        return VersionDetails(
            instance=self,
            fields=[
                VersionField(group_name="General", name="name", raw_value=self.name),
                VersionField(group_name="General", name="files", queryset=self.files.all()),
            ],
        )

    @transaction.atomic()
    def create_new_version(self, save=True):
        version_number = self.version_number
        self.version_number = version_number + 1
        self.save(update_fields=["version_number"])

        new_version = super().create_new_version(save=False)
        new_version.version_number = version_number
        new_version.save()

        file_versions = []
        for file in self.files.iterator(chunk_size=15):
            file_versions.append(file.create_new_version())

        new_version.files.add(*file_versions)
        return new_version

    def get_absolute_url(self):
        return reverse("documents:collections", args=[self.team.slug, "collections"])
