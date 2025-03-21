from django.db import models

from apps.teams.models import BaseTeamModel
from apps.utils.conversions import bytes_to_megabytes


class Collection(BaseTeamModel):
    name = models.CharField(max_length=255)
    files = models.ManyToManyField("files.File", blank=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["team", "name"], name="unique_collection_per_team")]

    @property
    def size(self) -> float:
        """Returns the size of this collection in megabytes"""
        bytes = self.files.aggregate(bytes=models.Sum("content_size"))["bytes"] or 0
        return bytes_to_megabytes(bytes)

    @property
    def file_names(self) -> list[str]:
        return list(self.files.values_list("name", flat=True))
