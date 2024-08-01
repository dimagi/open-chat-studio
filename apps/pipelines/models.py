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


class PipelineManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().prefetch_related("node_set")


class Pipeline(BaseTeamModel):
    name = models.CharField(max_length=128)
    data = models.JSONField()

    objects = PipelineManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("pipelines:details", args=[self.team.slug, self.id])

    def set_nodes(self, nodes):
        # Add new nodes, update old nodes, remove deleted nodes

        # Delete old nodes
        current_ids = set(self.node_set.values_list("flow_id", flat=True).all())
        new_ids = set(node.id for node in nodes)
        to_delete = current_ids - new_ids
        Node.objects.filter(pipeline=self, flow_id__in=to_delete).delete()

        for node in nodes:
            node_object, _ = Node.objects.get_or_create(pipeline=self, flow_id=node.id)
            node_object.type = node.data.get("type")
            node_object.params = node.data.get("params", {})
            node_object.save()

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


class Node(BaseModel):
    flow_id = models.CharField(max_length=128, db_index=True)  # The ID assigned by react-flow
    type = models.CharField(max_length=128)  # The node type, should be one from nodes/nodes.py
    params = models.JSONField(default=dict)  # Parameters for the specific node type

    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE)

    def __str__(self):
        return self.flow_id


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
