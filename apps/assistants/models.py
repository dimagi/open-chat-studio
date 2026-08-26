"""Data models for the retired OpenAI Assistants feature.

The behaviour is gone (issue #4254, phase 1) — these models survive only so the rows and the
Django admin remain available during the cool-off period. Phase 2 drops them along with the
``Node.assistant`` and ``CustomActionOperation.assistant`` FKs.
"""

from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.experiments.versioning import VersionsMixin, VersionsObjectManagerMixin
from apps.teams.models import BaseTeamModel
from apps.utils.fields import SanitizedJSONField
from apps.utils.models import BaseModel


class OpenAiAssistantManager(VersionsObjectManagerMixin, AuditingManager):
    pass


@audit_fields(
    "assistant_id",
    "name",
    "instructions",
    "builtin_tools",
    "llm_provider",
    "llm_provider_model",
    "temperature",
    "top_p",
    audit_special_queryset_writes=True,
)
class OpenAiAssistant(BaseTeamModel, VersionsMixin):
    assistant_id = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    instructions = models.TextField()
    temperature = models.FloatField(default=1.0, validators=[MinValueValidator(0.0), MaxValueValidator(2.0)])
    top_p = models.FloatField(default=1.0, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)])
    builtin_tools = ArrayField(models.CharField(max_length=128), default=list, blank=True)
    include_file_info = models.BooleanField(default=True)
    llm_provider = models.ForeignKey(
        "service_providers.LlmProvider",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="LLM Provider",
    )
    llm_provider_model = models.ForeignKey(
        "service_providers.LlmProviderModel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="The LLM model to use",
        verbose_name="LLM Model",
    )
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField(default=1)
    is_archived = models.BooleanField(default=False)
    tools = ArrayField(models.CharField(max_length=128), default=list, blank=True)

    allow_file_search_attachments = models.BooleanField(default=True)
    allow_code_interpreter_attachments = models.BooleanField(default=True)
    allow_file_downloads = models.BooleanField(default=False)
    objects = OpenAiAssistantManager()
    all_objects = AuditingManager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


@audit_fields(
    "assistant_id",
    "tool_type",
    "extra",
    audit_special_queryset_writes=True,
)
class ToolResources(BaseModel):
    assistant = models.ForeignKey(OpenAiAssistant, on_delete=models.CASCADE, related_name="tool_resources")
    tool_type = models.CharField(max_length=128)
    files = models.ManyToManyField("files.File", blank=True)
    extra = SanitizedJSONField(default=dict, blank=True)

    objects = AuditingManager()

    @property
    def label(self):
        return self.tool_type.replace("_", " ").title()

    def __str__(self):
        return f"Tool Resources for {self.assistant.name}: {self.tool_type}"
