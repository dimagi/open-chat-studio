from django.db import models

from apps.pipelines.models import Node
from apps.pipelines.nodes.nodes import LLMResponseWithPrompt
from apps.teams.models import BaseTeamModel
from apps.utils.conversions import bytes_to_megabytes


class RepositoryType(models.TextChoices):
    COLLECTION = "collection", "Collection"


class Repository(BaseTeamModel):
    name = models.CharField(max_length=255)
    summary = models.TextField()
    type = models.CharField(choices=RepositoryType.choices, default=RepositoryType.COLLECTION)
    files = models.ManyToManyField("files.File", blank=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["team", "type", "name"], name="unique_repository_per_team")]

    @property
    def size(self) -> float:
        """Returns the size of this repository in megabytes"""
        return bytes_to_megabytes(sum([bytes for bytes in self.files.values_list("content_size", flat=True)]))

    def file_names(self) -> list[str]:
        return list(self.files.values_list("name", flat=True))

    def get_references(self) -> list[Node]:
        return Node.objects.filter(type=LLMResponseWithPrompt.__name__, params__collection_id=str(self.id)).all()
