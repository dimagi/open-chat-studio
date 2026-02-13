from django.db import models
from pydantic import TypeAdapter

from apps.evaluations.field_definitions import FieldDefinition
from apps.teams.models import BaseTeamModel
from apps.utils.fields import SanitizedJSONField


class AnnotationSchema(BaseTeamModel):
    """Defines the fields annotators will fill out. Reuses FieldDefinition from evaluations."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    schema = SanitizedJSONField(
        default=dict,
        help_text="Dict of field_name -> FieldDefinition JSON (same format as evaluator output_schema)",
    )

    class Meta:
        unique_together = ("team", "name")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_field_definitions(self) -> dict[str, FieldDefinition]:
        """Parse the raw JSON schema into typed FieldDefinition objects."""
        adapter = TypeAdapter(FieldDefinition)
        return {name: adapter.validate_python(defn) for name, defn in self.schema.items()}
