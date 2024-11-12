from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.urls import reverse
from field_audit import audit_fields
from field_audit.models import AuditingManager

from apps.chat.agent.tools import get_assistant_tools
from apps.experiments.models import VersionsMixin
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel


class OpenAiAssistantManager(AuditingManager):
    pass


@audit_fields(
    "assistant_id",
    "name",
    "instructions",
    "builtin_tools",
    "llm_provider",
    "llm_model",
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
    llm_model = models.CharField(
        max_length=255,
        help_text="The LLM model to use.",
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

    files = models.ManyToManyField("files.File", blank=True)

    objects = OpenAiAssistantManager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("assistants:edit", args=[self.team.slug, self.id])

    @property
    def formatted_tools(self):
        return [{"type": tool} for tool in self.builtin_tools]

    def get_assistant(self):
        return self.llm_provider.get_llm_service().get_assistant(self.assistant_id, as_agent=True)

    def supports_code_interpreter(self):
        return "code_interpreter" in self.builtin_tools

    def supports_file_search(self):
        return "file_search" in self.builtin_tools

    @property
    def tools_enabled(self):
        return len(self.tools) > 0 or self.has_custom_actions()

    def has_custom_actions(self):
        return self.custom_action_operations.exists()

    @transaction.atomic()
    def create_new_version(self, *args, **kwargs):
        from .sync import push_assistant_to_openai

        version_number = self.version_number
        self.version_number = version_number + 1
        self.save(update_fields=["version_number"])
        assistant_version = super().create_new_version(save=False, *args, **kwargs)
        assistant_version.version_number = version_number
        assistant_version.save()

        assistant_version.files.set(self.files.all())
        assistant_version.tools = self.tools.copy()
        for tool_resource in self.tool_resources.all():
            files = tool_resource.files.all()
            if files:
                new_tool_resource = ToolResources.objects.create(
                    assistant=assistant_version,
                    tool_type=tool_resource.tool_type,
                )
                new_tool_resource.files.set(files)
                new_tool_resource.extra = tool_resource.extra
                new_tool_resource.save()

        push_assistant_to_openai(assistant_version, internal_tools=get_assistant_tools(assistant_version))
        return assistant_version


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
    extra = models.JSONField(default=dict, blank=True)

    objects = AuditingManager()

    @property
    def label(self):
        return self.tool_type.replace("_", " ").title()

    def __str__(self):
        return f"Tool Resources for {self.assistant.name}: {self.tool_type}"
