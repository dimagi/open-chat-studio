import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.experiments.models import Experiment, ExperimentSession
from apps.teams.models import BaseTeamModel


class AnalysisStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class TranscriptAnalysis(BaseTeamModel):
    """
    Stores a transcript analysis job.
    """

    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="transcript_analyses")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=AnalysisStatus.choices, default=AnalysisStatus.PENDING)
    sessions = models.ManyToManyField(ExperimentSession, related_name="analyses")
    query_file = models.FileField(upload_to="analysis_queries/")
    result_file = models.FileField(upload_to="analysis_results/", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    error_message = models.TextField(blank=True)
    job_id = models.CharField(max_length=255, blank=True)
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("analysis:detail", args=[self.team.slug, self.id])

    @property
    def is_complete(self):
        return self.status == AnalysisStatus.COMPLETED

    @property
    def is_failed(self):
        return self.status == AnalysisStatus.FAILED

    @property
    def is_processing(self):
        return self.status == AnalysisStatus.PROCESSING


class AnalysisQuery(models.Model):
    """
    Stores a single query for transcript analysis.
    """

    analysis = models.ForeignKey(TranscriptAnalysis, on_delete=models.CASCADE, related_name="queries")
    name = models.CharField(max_length=255, blank=True)
    prompt = models.TextField()
    output_format = models.CharField(max_length=255, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        if self.name:
            return self.name
        return f"{self.prompt[:50]}..."
