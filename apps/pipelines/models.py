from datetime import datetime

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel as PydanticBaseModel

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
        from apps.pipelines.logging import PipelineLoggingCallbackHandler, get_logger

        runnable = PipelineGraph.build_runnable_from_json(self.data)
        pipeline_run = PipelineRun.objects.create(pipeline=self, status=PipelineRunStatus.RUNNING)
        logger = get_logger("test", pipeline_run)
        output = runnable.invoke(
            input, config=RunnableConfig(callbacks=[PipelineLoggingCallbackHandler(self, pipeline_run, logger)])
        )
        # pipeline_run.save()
        return output


class PipelineRunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    ERROR = "error", "Error"


class LogEntry(PydanticBaseModel):
    time: datetime
    level: str
    message: str
    # file: str
    # line: int

    class Config:
        json_encoders = {datetime: lambda v: v.strftime("%Y-%m-%d %H:%M:%S.%f")}


class PipelineRun(BaseModel):
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name="runs")

    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=128, choices=PipelineRunStatus.choices)
    error = models.TextField(blank=True)
    output_summary = models.TextField(blank=True)
    log = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)

    class Meta:
        ordering = ["created_at"]

    def append_log(self, log_entry: LogEntry):
        entries = self.log.get("entries", [])
        self.log = {"entries": [*entries, log_entry.model_dump()]}
