from datetime import datetime

import pydantic
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.urls import reverse
from langchain_core.runnables import RunnableConfig

from apps.pipelines.logging import PipelineLoggingCallbackHandler
from apps.pipelines.nodes.base import PipelineState
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel


class Pipeline(BaseTeamModel):
    name = models.CharField(max_length=128)
    data = models.JSONField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("pipelines:details", args=[self.team.slug, self.id])

    def invoke(self, input: PipelineState) -> PipelineState:
        from apps.pipelines.graph import PipelineGraph

        runnable = PipelineGraph.build_runnable_from_json(self.data)

        pipeline_run = PipelineRun.objects.create(
            pipeline=self, input=input, status=PipelineRunStatus.RUNNING, log={"entries": []}
        )

        logging_callback = PipelineLoggingCallbackHandler(pipeline_run)
        logging_callback.logger.info("Starting pipeline run")
        try:
            output = runnable.invoke(input, config=RunnableConfig(callbacks=[logging_callback]))
            pipeline_run.output = output
        finally:
            logging_callback.logger.info("Pipeline run finished")
            pipeline_run.save()
        return output


class PipelineRunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    ERROR = "error", "Error"


class PipelineRun(BaseModel):
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=128, choices=PipelineRunStatus.choices)
    input = models.JSONField(blank=True, null=True, encoder=DjangoJSONEncoder)
    output = models.JSONField(blank=True, null=True, encoder=DjangoJSONEncoder)
    log = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)

    def get_absolute_url(self):
        return reverse("pipelines:run_details", args=[self.pipeline.team.slug, self.pipeline_id, self.id])


class LogEntry(pydantic.BaseModel):
    time: datetime
    level: str
    message: str

    class Config:
        json_encoders = {datetime: lambda v: v.strftime("%Y-%m-%d %H:%M:%S.%f")}
