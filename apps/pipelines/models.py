from collections.abc import Iterator
from datetime import datetime
from functools import cached_property

import pydantic
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.urls import reverse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ConfigDict

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.pipelines.flow import Flow, FlowNode, FlowNodeData
from apps.pipelines.logging import PipelineLoggingCallbackHandler
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.utils import get_input_types_for_node
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

    def set_nodes(self, nodes: list[FlowNode]) -> None:
        """Set the nodes on the pipeline from data coming from the frontend"""

        # Delete old nodes
        current_ids = set(self.node_ids)
        new_ids = set(node.id for node in nodes)
        to_delete = current_ids - new_ids
        Node.objects.filter(pipeline=self, flow_id__in=to_delete).delete()

        # Set new nodes or update existing ones
        for node in nodes:
            Node.objects.update_or_create(
                pipeline=self,
                flow_id=node.id,
                defaults={
                    "type": node.data.type,
                    "params": node.data.params,
                    "label": node.data.label,
                },
            )

    @cached_property
    def flow_data(self) -> dict:
        from apps.pipelines.nodes import nodes as pipeline_nodes

        flow = Flow(**self.data)
        flow_nodes_by_id = {node.id: node for node in flow.nodes}
        nodes = []
        for node in self.node_set.all():
            node_class = getattr(pipeline_nodes, node.type)
            input_types = get_input_types_for_node(node_class)
            nodes.append(
                FlowNode(
                    id=node.flow_id,
                    position=flow_nodes_by_id[node.flow_id].position,
                    data=FlowNodeData(
                        id=node.flow_id,
                        type=node.type,
                        label=node.label,
                        params=node.params,
                        inputParams=input_types["input_params"],
                    ),
                )
            )
        flow.nodes = nodes
        return flow.model_dump()

    @cached_property
    def node_ids(self):
        return self.node_set.values_list("flow_id", flat=True).all()

    def invoke(
        self, input: PipelineState, session: ExperimentSession, save_run_to_history: bool = True
    ) -> PipelineState:
        from apps.pipelines.graph import PipelineGraph

        runnable = PipelineGraph.build_runnable_from_pipeline(self)
        pipeline_run = self._create_pipeline_run(input, session)
        logging_callback = PipelineLoggingCallbackHandler(pipeline_run)

        logging_callback.logger.debug("Starting pipeline run", input=input["messages"][-1])
        try:
            output = runnable.invoke(input, config=RunnableConfig(callbacks=[logging_callback]))
            output = PipelineState(**output).json_safe()
            pipeline_run.output = output
            if save_run_to_history and session is not None:
                self._save_message_to_history(session, input["messages"][-1], ChatMessageType.HUMAN)
                self._save_message_to_history(session, output["messages"][-1], ChatMessageType.AI)
        finally:
            if pipeline_run.status == PipelineRunStatus.ERROR:
                logging_callback.logger.debug("Pipeline run failed", input=input["messages"][-1])
            else:
                pipeline_run.status = PipelineRunStatus.SUCCESS
                logging_callback.logger.debug("Pipeline run finished", output=output["messages"][-1])
            pipeline_run.save()
        return output

    def _create_pipeline_run(self, input: PipelineState, session: ExperimentSession) -> "PipelineRun":
        # Django doesn't auto-serialize objects for JSON fields, so we need to copy the input and save the ID of
        # the session instead of the session object.

        return PipelineRun.objects.create(
            pipeline=self,
            input=input.json_safe(),
            status=PipelineRunStatus.RUNNING,
            log={"entries": []},
            session=session,
        )

    def _save_message_to_history(self, session: ExperimentSession, message: str, type_: ChatMessageType):
        ChatMessage.objects.create(
            chat=session.chat,
            message_type=type_.value,
            content=message,
        )
        # TODO: Add tags here?


class Node(BaseModel):
    flow_id = models.CharField(max_length=128, db_index=True)  # The ID assigned by react-flow
    type = models.CharField(max_length=128)  # The node type, should be one from nodes/nodes.py
    label = models.CharField(max_length=128, blank=True, default="")  # The human readable label
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
    model_config = ConfigDict(json_encoders={datetime: lambda v: v.strftime("%Y-%m-%d %H:%M:%S.%f")})

    time: datetime
    level: str
    message: str
    output: str | None = None
    input: str | None = None


class PipelineEventInputs(models.TextChoices):
    FULL_HISTORY = "full_history", "Full History"
    HISTORY_LAST_SUMMARY = "history_last_summary", "History to last summary"
    LAST_MESSAGE = "last_message", "Last message"


class PipelineChatHistoryTypes(models.TextChoices):
    NODE = "node", "Node History"
    NAMED = "named", "Named History"
    GLOBAL = "global", "Global History"
    NONE = "none", "No History"


class PipelineChatHistory(BaseModel):
    session = models.ForeignKey(ExperimentSession, on_delete=models.CASCADE, related_name="pipeline_chat_history")

    type = models.CharField(max_length=10, choices=PipelineChatHistoryTypes.choices)
    name = models.CharField(max_length=128, db_index=True)  # Either the name of the named history, or the node id

    def __str__(self):
        return f"Session: {self.session_id}, Type: {self.type}, Name: {self.name}"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("session", "type", "name"), name="unique_session_type_name"),
        ]
        ordering = ["-created_at"]

    def message_iterator(self) -> Iterator["PipelineChatMessages"]:
        yield from self.messages.order_by("-created_at").iterator(100)

    def get_messages_until_summary(self):
        messages = []
        for message in self.message_iterator():
            messages.append(message)
            if message.summary:
                break
        return messages

    def get_langchain_messages_until_summary(self):
        messages = self.get_messages_until_summary()
        langchain_messages_to_last_summary = [
            message for message_pair in messages for message in message_pair.as_langchain_messages()
        ]
        return list(reversed(langchain_messages_to_last_summary))


class PipelineChatMessages(BaseModel):
    chat_history = models.ForeignKey(PipelineChatHistory, on_delete=models.CASCADE, related_name="messages")
    human_message = models.TextField()
    ai_message = models.TextField()
    summary = models.TextField(null=True)  # noqa: DJ001

    def __str__(self):
        if self.summary:
            return f"Human: {self.human_message}, AI: {self.ai_message}, System: {self.summary}"
        return f"Human: {self.human_message}, AI: {self.ai_message}"

    def as_tuples(self):
        message_tuples = []
        if self.summary:
            message_tuples.append((ChatMessageType.SYSTEM.value, self.summary))
        message_tuples.extend(
            [
                (ChatMessageType.HUMAN.value, self.human_message),
                (ChatMessageType.AI.value, self.ai_message),
            ]
        )
        return message_tuples

    def as_langchain_messages(self) -> list[BaseMessage]:
        langchain_messages = [
            AIMessage(content=self.ai_message, additional_kwargs={"id": self.id}),
            HumanMessage(content=self.human_message, additional_kwargs={"id": self.id}),
        ]
        if self.summary:
            langchain_messages.append(SystemMessage(content=self.summary, additional_kwargs={"id": self.id}))

        return langchain_messages
