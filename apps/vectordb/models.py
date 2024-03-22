from django.db import models
from pgvector.django import VectorField

from apps.teams.models import BaseTeamModel

ADA_TOKEN_COUNT = 1536


class Embedding(BaseTeamModel):
    experiment = models.ForeignKey("experiments.Experiment", on_delete=models.CASCADE, related_name="embeddings")
    embedding = VectorField(dimensions=ADA_TOKEN_COUNT)
    document = models.TextField(null=True)  # noqa: DJ001
    metadata = models.JSONField(null=True)
    file = models.ForeignKey("files.File", on_delete=models.CASCADE, null=True, blank=True)
