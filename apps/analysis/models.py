import json
import math
from datetime import timedelta

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.teams.models import BaseTeamModel


class ResourceType(models.TextChoices):
    TEXT = "text", "Text"
    CSV = "csv", "CSV"
    JSON = "json", "JSON"
    JSONL = "jsonl", "JSON Lines"
    XML = "xml", "XML"
    XLSX = "xlsx", "XLSX"
    IMAGE = "image", "Image"


class Resource(BaseTeamModel):
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=128, choices=ResourceType.choices)
    file = models.FileField()
    content_size = models.PositiveIntegerField()

    def save(self, *args, **kwargs):
        self.content_size = self.file.size
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"


class Analysis(BaseTeamModel):
    name = models.CharField(max_length=255)
    source = models.CharField(max_length=255, help_text="Name of the pipeline source")
    pipelines = ArrayField(models.CharField(max_length=255), default=list, help_text="List of pipeline names")
    llm_provider = models.ForeignKey("service_providers.LlmProvider", on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("analysis:details", args=[self.team.slug, self.id])


class RunStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    ERROR = "error", "Error"


class AnalysisRun(BaseTeamModel):
    analysis = models.ForeignKey(Analysis, on_delete=models.CASCADE)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=128, choices=RunStatus.choices, default=RunStatus.PENDING)
    output = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    log = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    params = models.JSONField(default=dict, blank=True)
    resources = models.ManyToManyField(Resource)
    task_id = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"{self.analysis.name}: {self.id}"

    def get_absolute_url(self):
        return reverse("analysis:run_details", args=[self.team.slug, self.id])

    @property
    def is_complete(self):
        return self.status in (RunStatus.SUCCESS, RunStatus.ERROR)

    def get_output_display(self):
        return json.dumps(self.output, indent=2)

    def get_params_display(self):
        return json.dumps(self.params, indent=2)

    @property
    def duration(self) -> timedelta | None:
        if self.start_time and self.end_time:
            return self.end_time - self.start_time

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
