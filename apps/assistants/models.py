from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.urls import reverse
from field_audit import audit_fields
from field_audit.models import AuditAction, AuditingManager

from apps.chat.agent.tools import get_assistant_tools
from apps.custom_actions.mixins import CustomActionOperationMixin
from apps.experiments.models import Experiment
from apps.experiments.versioning import VersionDetails, VersionField, VersionsMixin, VersionsObjectManagerMixin
from apps.pipelines.models import Node
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
class OpenAiAssistant(BaseTeamModel, VersionsMixin, CustomActionOperationMixin):
    ALLOWED_INSTRUCTIONS_VARIABLES = {"participant_data", "current_datetime"}

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

    def get_absolute_url(self):
        return reverse("assistants:edit", args=[self.team.slug, self.id])

    @property
    def formatted_tools(self):
        return [{"type": tool} for tool in self.builtin_tools]

    def get_llm_service(self):
        return self.llm_provider.get_llm_service()

    def get_assistant(self):
        return self.get_llm_service().get_assistant(self.assistant_id, as_agent=True)

    def supports_code_interpreter_attachments(self):
        return "code_interpreter" in self.builtin_tools and self.allow_code_interpreter_attachments

    def supports_file_search_attachments(self):
        return "file_search" in self.builtin_tools and self.allow_file_search_attachments

    @property
    def tools_enabled(self):
        return len(self.tools) > 0 or self.has_custom_actions()

    def has_custom_actions(self):
        return self.custom_action_operations.exists()

    @transaction.atomic()
    def create_new_version(self):
        from .sync import push_assistant_to_openai

        version_number = self.version_number
        self.version_number = version_number + 1
        self.save(update_fields=["version_number"])
        assistant_version = super().create_new_version(save=False)
        assistant_version.version_number = version_number
        assistant_version.name = f"{self.name} v{version_number}"
        assistant_version.assistant_id = ""
        assistant_version.save()
        assistant_version.tools = self.tools.copy()
        for tool_resource in self.tool_resources.all():
            new_tool_resource = ToolResources.objects.create(
                assistant=assistant_version,
                tool_type=tool_resource.tool_type,
                extra=tool_resource.extra,
            )
            if tool_resource.tool_type == "file_search":
                # Clear the vector store ID so that a new one will be created
                new_tool_resource.extra["vector_store_id"] = None
            new_tool_resource.save()

            new_tool_resource.files.set(tool_resource.files.all())

        self._copy_custom_action_operations_to_new_version(new_assistant=assistant_version)

        push_assistant_to_openai(assistant_version, internal_tools=get_assistant_tools(assistant_version))
        return assistant_version

    def archive(self):
        from apps.assistants.tasks import delete_openai_assistant_task

        if self._is_actively_used:
            return False

        if self.is_working_version:
            for (
                version
            ) in self.versions.all():  # first perform all checks so assistants are not archived prior to return False
                if version._is_actively_used:
                    return False

            for version in self.versions.all():
                delete_openai_assistant_task.delay(version.id)
            self.versions.update(is_archived=True, audit_action=AuditAction.AUDIT)

        super().archive()
        delete_openai_assistant_task.delay(self.id)
        return True

    def get_related_experiments_queryset(self, assistant_ids: list = None):
        """Returns working versions and published experiments containing the assistant ids"""
        if assistant_ids:
            return Experiment.objects.filter(
                Q(working_version_id=None) | Q(is_default_version=True),
                assistant_id__in=assistant_ids,
                is_archived=False,
            )

        return self.experiment_set.filter(Q(working_version_id=None) | Q(is_default_version=True), is_archived=False)

    def get_related_pipeline_node_queryset(self, assistant_ids: list = None):
        """Returns working version pipelines with assistant nodes containing the assistant ids"""
        assistant_ids = assistant_ids if assistant_ids else [str(self.id)]
        return Node.objects.assistant_nodes().filter(
            pipeline__working_version_id=None,
            params__assistant_id__in=assistant_ids,
            is_archived=False,
            pipeline__is_archived=False,
        )

    def get_related_experiments_with_pipeline_queryset(self, assistant_ids: list = None):
        """Returns published experiment versions referenced by versioned pipelines with assistant nodes
        containing the assistant ids"""
        assistant_ids = assistant_ids if assistant_ids else [str(self.id)]
        nodes = Node.objects.assistant_nodes().filter(
            pipeline__working_version_id__isnull=False,
            params__assistant_id__in=assistant_ids,
            is_archived=False,
            pipeline__is_archived=False,
        )

        if pipeline_ids := nodes.values_list("pipeline_id", flat=True):
            return Experiment.objects.filter(
                is_default_version=True,
                pipeline_id__in=pipeline_ids,
                is_archived=False,
            )
        return Experiment.objects.none()

    @property
    def _is_actively_used(self) -> bool:
        """Check if the assistant is actively used in any experiments or pipelines"""
        return (
            self.get_related_experiments_queryset().exists()
            or self.get_related_pipeline_node_queryset().exists()
            or self.get_related_experiments_with_pipeline_queryset().exists()
        )

    def _get_version_details(self) -> VersionDetails:
        from apps.experiments.models import VersionFieldDisplayFormatters

        return VersionDetails(
            instance=self,
            fields=[
                VersionField(group_name="General", name="name", raw_value=self.name.split(" v")[0]),
                VersionField(group_name="General", name="include_file_info", raw_value=self.include_file_info),
                VersionField(group_name="Language Model", name="llm_provider", raw_value=self.llm_provider),
                VersionField(group_name="Language Model", name="llm_provider_model", raw_value=self.llm_provider_model),
                VersionField(group_name="Language Model", name="temperature", raw_value=self.temperature),
                VersionField(group_name="Language Model", name="top_p", raw_value=self.top_p),
                VersionField(group_name="Language Model", name="instructions", raw_value=self.instructions),
                VersionField(
                    group_name="Tools",
                    name="builtin_tools",
                    raw_value=self.builtin_tools,
                    to_display=VersionFieldDisplayFormatters.format_builtin_tools,
                ),
                VersionField(
                    group_name="Tools",
                    name="tools",
                    raw_value=self.tools,
                    to_display=VersionFieldDisplayFormatters.format_tools,
                ),
                VersionField(
                    group_name="Tools",
                    name="allow_file_search_attachments",
                    raw_value=self.allow_file_search_attachments,
                ),
                VersionField(
                    group_name="Tools",
                    name="allow_code_interpreter_attachments",
                    raw_value=self.allow_code_interpreter_attachments,
                ),
            ],
        )


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
