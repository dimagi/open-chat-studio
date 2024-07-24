from datetime import datetime
from functools import cached_property

import pydantic
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.urls import reverse
from langchain_core.runnables import RunnableConfig

from apps.experiments.models import ExperimentSession
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

    @cached_property
    def node_ids(self):
        return [node.get("data", {}).get("id") for node in self.data.get("nodes", [])]

    def invoke(self, input: PipelineState, session: ExperimentSession | None = None) -> PipelineState:
        from apps.pipelines.graph import PipelineGraph

        runnable = PipelineGraph.build_runnable_from_json(self.data)
        # Django doesn't auto-serialize objects for JSON fields, so we need to copy the input and save the ID of
        # the session instead of the session object.
        pipeline_run = PipelineRun.objects.create(
            pipeline=self,
            input=input.json_safe(),
            status=PipelineRunStatus.RUNNING,
            log={"entries": []},
            session=session,
        )

        logging_callback = PipelineLoggingCallbackHandler(pipeline_run)
        logging_callback.logger.debug("Starting pipeline run", input=input["messages"][-1])
        try:
            output = runnable.invoke(input, config=RunnableConfig(callbacks=[logging_callback]))
            output = PipelineState(**output).json_safe()
            pipeline_run.output = output
        finally:
            if pipeline_run.status == PipelineRunStatus.ERROR:
                logging_callback.logger.debug("Pipeline run failed", input=input["messages"][-1])
            else:
                pipeline_run.status = PipelineRunStatus.SUCCESS
                logging_callback.logger.debug("Pipeline run finished", output=output["messages"][-1])
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
    session = models.ForeignKey(ExperimentSession, on_delete=models.SET_NULL, related_name="pipeline_runs", null=True)

    def get_absolute_url(self):
        return reverse("pipelines:run_details", args=[self.pipeline.team.slug, self.pipeline_id, self.id])


class LogEntry(pydantic.BaseModel):
    time: datetime
    level: str
    message: str
    output: str | None = None
    input: str | None = None

    class Config:
        json_encoders = {datetime: lambda v: v.strftime("%Y-%m-%d %H:%M:%S.%f")}


class PipelineEventInputs(models.TextChoices):
    FULL_HISTORY = "full_history", "Full History"
    HISTORY_LAST_SUMMARY = "history_last_summary", "History to last summary"
    LAST_MESSAGE = "last_message", "Last message"
