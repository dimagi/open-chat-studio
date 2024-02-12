import json
import math
from datetime import timedelta

import pydantic
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.analysis.log import LogEntry
from apps.teams.models import BaseTeamModel, Team
from apps.utils.models import BaseModel


class ResourceType(models.TextChoices):
    TEXT = "text", "Text"
    CSV = "csv", "CSV"
    JSON = "json", "JSON"
    JSONL = "jsonl", "JSON Lines"
    XML = "xml", "XML"
    XLSX = "xlsx", "XLSX"
    IMAGE = "image", "Image"
    UNKNOWN = "unknown", "Unknown"


class ResourceMetadata(pydantic.BaseModel):
    type: str
    format: ResourceType
    data_schema: dict
    openai_file_id: str | None = None
    content_type: str | None = None

    def get_label(self):
        return f"{self.type} ({self.format})"


class Resource(BaseTeamModel):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=128, choices=ResourceType.choices)
    file = models.FileField()
    metadata = models.JSONField(default=dict, blank=True)
    content_size = models.PositiveIntegerField(null=True, blank=True)
    content_type = models.CharField(null=True, blank=True)  # noqa DJ001

    def save(self, *args, **kwargs):
        if self.file:
            self.content_size = self.file.size
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    @property
    def wrapped_metadata(self):
        return ResourceMetadata(**{"content_type": self.content_type, **self.metadata})


class Analysis(BaseTeamModel):
    name = models.CharField(max_length=255)
    source = models.CharField(max_length=255, help_text="Name of the pipeline source")
    pipeline = models.CharField(max_length=255, help_text="Data processing pipeline")
    llm_provider = models.ForeignKey("service_providers.LlmProvider", on_delete=models.SET_NULL, null=True, blank=True)
    llm_model = models.CharField(
        max_length=20,
        help_text="The LLM model to use.",
        verbose_name="LLM Model",
    )
    config = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("analysis:details", args=[self.team.slug, self.id])

    def needs_configuration(self):
        from apps.analysis.pipelines import get_static_forms_for_analysis

        return not self.config and get_static_forms_for_analysis(self)


class RunStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    ERROR = "error", "Error"
    CANCELLING = "cancelling", "Cancelling"
    CANCELLED = "cancelled", "Cancelled"


class BaseRun(BaseModel):
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=128, choices=RunStatus.choices, default=RunStatus.PENDING)
    error = models.TextField(blank=True)
    task_id = models.CharField(max_length=255, null=True, blank=True)  # noqa DJ001

    class Meta:
        abstract = True

    @property
    def is_complete(self):
        return self.status in (RunStatus.SUCCESS, RunStatus.ERROR, RunStatus.CANCELLED)

    @property
    def is_cancelling(self):
        return self.status == RunStatus.CANCELLING

    @property
    def is_cancelled(self):
        return self.status in (RunStatus.CANCELLING, RunStatus.CANCELLED)

    @property
    def is_running(self):
        return self.status in (RunStatus.RUNNING, RunStatus.PENDING) and not self.is_expired

    @property
    def is_expired(self):
        return self.start_time and (timezone.now() - self.start_time) > timedelta(hours=1)

    @property
    def duration(self) -> timedelta | None:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        elif self.status == RunStatus.RUNNING:
            return timezone.now() - self.start_time

    @property
    def duration_seconds(self):
        duration = self.duration
        if duration is None:
            return None
        seconds = duration.total_seconds()
        if seconds < 60:
            return round(seconds, 2)
        return seconds

    def duration_display(self):
        seconds = self.duration_seconds
        if seconds is None:
            return ""

        if seconds < 60:
            return str(seconds) + "s"
        return timedelta(seconds=math.ceil(seconds))


class RunGroup(BaseRun):
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    analysis = models.ForeignKey(Analysis, on_delete=models.CASCADE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    params = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    notes = models.TextField(null=True, blank=True)  # noqa DJ001
    starred = models.BooleanField(default=False)
    approved = models.BooleanField(null=True, blank=True)

    @property
    def thumbs_up(self):
        return self.approved

    @property
    def thumbs_down(self):
        return self.approved is not None and not self.approved

    def get_params_display(self):
        return json.dumps(self.params, indent=2)

    def get_absolute_url(self):
        return reverse("analysis:group_details", args=[self.team.slug, self.id])


class AnalysisRun(BaseRun):
    name = models.CharField(max_length=255, blank=True)
    group = models.ForeignKey(RunGroup, on_delete=models.CASCADE)
    output_summary = models.TextField(blank=True)
    log = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    metadata = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    input_resource = models.ForeignKey(Resource, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    output_resources = models.ManyToManyField(Resource, related_name="+")

    def __str__(self):
        return f"{self.group.analysis.name}: {self.id}"

    class Meta:
        ordering = ["created_at"]

    def get_log_entries(self):
        return [LogEntry.from_json(entry) for entry in self.log.get("entries", [])]
