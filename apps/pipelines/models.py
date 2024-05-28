from datetime import datetime

import pydantic
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from langchain_core.runnables import RunnableConfig

from apps.pipelines.logging import PipelineLoggingCallbackHandler
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel


class Pipeline(BaseTeamModel):
    name = models.CharField(max_length=128)
    data = models.JSONField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def invoke(self, input):
        from apps.pipelines.graph import PipelineGraph

        runnable = PipelineGraph.build_runnable_from_json(self.data)

        pipeline_run = PipelineRun.objects.create(pipeline=self, status=PipelineRunStatus.RUNNING, log={"entries": []})
        logging_callback = PipelineLoggingCallbackHandler(pipeline_run)
        try:
            output = runnable.invoke(input, config=RunnableConfig(callbacks=[logging_callback]))
        finally:
            logging_callback.pipeline_run.save()
        return output


class PipelineRunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    ERROR = "error", "Error"


class PipelineRun(BaseModel):
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField()
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=128, choices=PipelineRunStatus.choices)
    error = models.TextField(blank=True)
    output_summary = models.TextField(blank=True)
    log = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)


class LogEntry(pydantic.BaseModel):
    time: datetime
    level: str
    message: str
    # file: str
    # line: int

    class Config:
        json_encoders = {datetime: lambda v: v.strftime("%Y-%m-%d %H:%M:%S.%f")}
