from django.db import models

from apps.experiments.versioning import VersionsMixin, VersionsObjectManagerMixin
from apps.pipelines.models import Node
from apps.teams.models import BaseTeamModel
from apps.utils.conversions import bytes_to_megabytes


class CollectionObjectManager(VersionsObjectManagerMixin, models.Manager):
    pass


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

    def get_references(self) -> list[Node]:
        return (
            Node.objects.llm_response_with_prompt_nodes()
            .select_related("pipeline")
            .filter(params__collection_id=str(self.id))
            .all()
        )
