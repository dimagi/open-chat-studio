from django.contrib.postgres.fields import ArrayField
from django.db import models

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


class RunStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    ERROR = "error", "Error"


class AnalysisRun(BaseTeamModel):
    analysis = models.ForeignKey(Analysis, on_delete=models.CASCADE)
    pipeline = models.CharField(max_length=255)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=128, choices=RunStatus.choices)
    output = models.JSONField(default=dict, blank=True)
    error = models.JSONField(default=dict, blank=True)
    log = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    params = models.JSONField(default=dict, blank=True)
    resources = models.ManyToManyField(Resource)
    source_data = models.JSONField(default=dict, blank=True)
