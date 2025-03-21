from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime
from functools import cached_property
from uuid import uuid4

import pydantic
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.urls import reverse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import ConfigDict

from apps.chat.models import ChatMessage, ChatMessageType
from apps.custom_actions.form_utils import set_custom_actions
from apps.custom_actions.mixins import CustomActionOperationMixin
from apps.experiments.models import ExperimentSession, VersionsMixin, VersionsObjectManagerMixin
from apps.experiments.versioning import VersionDetails, VersionField
from apps.pipelines.exceptions import PipelineBuildError
from apps.pipelines.executor import patch_executor
from apps.pipelines.flow import Flow, FlowNode, FlowNodeData
from apps.pipelines.logging import PipelineLoggingCallbackHandler
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.helpers import temporary_session
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel


class PipelineManager(VersionsObjectManagerMixin, models.Manager):
    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(
                is_version=models.Case(
                    models.When(working_version_id__isnull=False, then=True),
                    models.When(working_version_id__isnull=True, then=False),
                    output_field=models.BooleanField(),
                )
            )
        )


class NodeObjectManager(VersionsObjectManagerMixin, models.Manager):
    pass


class Pipeline(BaseTeamModel, VersionsMixin):
    name = models.CharField(max_length=128)
    data = models.JSONField()
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    version_number = models.PositiveIntegerField(default=1)
    is_archived = models.BooleanField(default=False)

    objects = PipelineManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        if self.working_version is None:
            return self.name
        return f"{self.name} ({self.version_display})"

    @property
    def version_display(self) -> str:
        if self.is_working_version:
            return ""
        return f"v{self.version_number}"

    @classmethod
    def create_default(cls, team):
        from apps.pipelines.nodes.nodes import EndNode, StartNode

        default_name = "New Pipeline"
        existing_pipeline_count = cls.objects.filter(team=team, name__startswith=default_name).count()

        start_id = str(uuid4())
        start_node = FlowNode(
            id=start_id,
            type="startNode",
            position={
                "x": -200,
                "y": 200,
            },
            data=FlowNodeData(id=start_id, type=StartNode.__name__, params={"name": "start"}),
        )
        end_id = str(uuid4())
        end_node = FlowNode(
            id=end_id,
            type="endNode",
            position={"x": 1000, "y": 200},
            data=FlowNodeData(id=end_id, type=EndNode.__name__, params={"name": "end"}),
        )
        default_nodes = [start_node.model_dump(), end_node.model_dump()]
        new_pipeline = cls.objects.create(
            team=team,
            data={"nodes": default_nodes, "edges": []},
            name=f"New Pipeline {existing_pipeline_count + 1}",
        )
        new_pipeline.update_nodes_from_data()
        return new_pipeline

    def get_absolute_url(self):
        return reverse("pipelines:details", args=[self.team.slug, self.id])

    def update_nodes_from_data(self) -> None:
        """Set the nodes on the pipeline from data coming from the frontend"""
        nodes = [FlowNode(**node) for node in self.data["nodes"]]
        # Delete old nodes
        current_ids = set(self.node_ids)
        new_ids = set(node.id for node in nodes)
        to_remove = current_ids - new_ids

        pipeline_nodes = Node.objects.annotate(versions_count=models.Count("versions")).filter(
            pipeline=self, flow_id__in=to_remove
        )
        nodes_to_archive = pipeline_nodes.filter(versions_count__gt=0)
        pipeline_nodes.filter(versions_count=0).delete()

        for node in nodes_to_archive:
            # Preserve the node if it has versions, otherwise we tamper with previous versions
            node.archive()

        # Set new nodes or update existing ones
        for node in nodes:
            created_node, _ = Node.objects.update_or_create(
                pipeline=self,
                flow_id=node.id,
                defaults={
                    "type": node.data.type,
                    "params": node.data.params,
                    "label": node.data.label,
                },
            )
            created_node.update_from_params()

    def validate(self, full=True) -> dict:
        """Validate the pipeline nodes and return a dictionary of errors"""
        from apps.pipelines.graph import PipelineGraph
        from apps.pipelines.nodes import nodes as pipeline_nodes

        errors = defaultdict(dict)
        nodes = self.node_set.all()
        for node in nodes:
            node_class = getattr(pipeline_nodes, node.type)
            try:
                node_class.model_validate(node.params)
            except pydantic.ValidationError as e:
                errors[node.flow_id] = {err["loc"][0]: err["msg"] for err in e.errors()}

        name_to_flow_id = defaultdict(list)
        for node in nodes:
            name_to_flow_id[node.params.get("name")].append(node.flow_id)

        for name, flow_ids in name_to_flow_id.items():
            if len(flow_ids) > 1:
                for flow_id in flow_ids:
                    errors[flow_id].update({"name": "All node names must be unique"})

        if errors:
            return {"node": errors}

        if full:
            try:
                PipelineGraph.build_runnable_from_pipeline(self)
            except PipelineBuildError as e:
                return e.to_json()

        return {}

    @cached_property
    def flow_data(self) -> dict:
        flow = Flow(**self.data)
        flow_nodes_by_id = {node.id: node for node in flow.nodes}
        nodes = []

        for node in self.node_set.all():
            nodes.append(
                FlowNode(
                    id=node.flow_id,
                    position=flow_nodes_by_id[node.flow_id].position,
                    type=flow_nodes_by_id[node.flow_id].type,
                    data=FlowNodeData(
                        id=node.flow_id,
                        type=node.type,
                        params=node.params,
                        label=node.label,
                    ),
                )
            )
        flow.nodes = nodes
        return flow.model_dump()

    @property
    def node_ids(self):
        return self.node_set.order_by("created_at").values_list("flow_id", flat=True).all()

    def simple_invoke(self, input: str, user_id: int) -> PipelineState:
        """Invoke the pipeline without a session or the ability to save the run to history"""
        from apps.pipelines.graph import PipelineGraph

        with temporary_session(self.team, user_id) as session:
            runnable = PipelineGraph.build_runnable_from_pipeline(self)
            input = PipelineState(messages=[input], experiment_session=session, pipeline_version=self.version_number)
            with patch_executor():
                output = runnable.invoke(input, config={"max_concurrency": 1})
            output = PipelineState(**output).json_safe()
        return output

    def invoke(
        self,
        input: PipelineState,
        session: ExperimentSession,
        save_run_to_history=True,
        save_input_to_history=True,
        disable_reminder_tools=False,
    ) -> dict:
        from apps.experiments.models import AgentTools
        from apps.pipelines.graph import PipelineGraph

        runnable = PipelineGraph.build_runnable_from_pipeline(self)
        pipeline_run = self._create_pipeline_run(input, session)
        logging_callback = PipelineLoggingCallbackHandler(pipeline_run)

        logging_callback.logger.debug("Starting pipeline run", input=input["messages"][-1])
        try:
            callbacks = [logging_callback]
            if trace_service := session.experiment.trace_service:
                trace_service_callback = trace_service.get_callback(
                    participant_id=str(session.participant.identifier),
                    session_id=str(session.external_id),
                )
                callbacks.append(trace_service_callback)

            config = RunnableConfig(
                callbacks=callbacks,
                configurable={
                    "disabled_tools": AgentTools.reminder_tools() if disable_reminder_tools else [],
                },
            )
            output = runnable.invoke(input, config=config)
            output = PipelineState(**output).json_safe()
            pipeline_run.output = output
            if save_run_to_history and session is not None:
                metadata = output.get("message_metadata", {})
                if save_input_to_history:
                    self._save_message_to_history(
                        session, input["messages"][-1], ChatMessageType.HUMAN, metadata=metadata.get("input", {})
                    )
                ai_message = self._save_message_to_history(
                    session, output["messages"][-1], ChatMessageType.AI, metadata=metadata.get("output", {})
                )
                output["ai_message_id"] = ai_message.id
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

    def _save_message_to_history(
        self, session: ExperimentSession, message: str, type_: ChatMessageType, metadata: dict
    ) -> ChatMessage:
        chat_message = ChatMessage.objects.create(
            chat=session.chat, message_type=type_.value, content=message, metadata=metadata
        )

        if type_ == ChatMessageType.AI:
            chat_message.add_version_tag(version_number=self.version_number, is_a_version=self.is_a_version)
        return chat_message

    @transaction.atomic()
    def create_new_version(self, *args, **kwargs):
        version_number = self.version_number
        self.version_number = version_number + 1
        self.save(update_fields=["version_number"])

        pipeline_version = super().create_new_version(save=False, *args, **kwargs)
        pipeline_version.version_number = version_number
        pipeline_version.save()

        for node in self.node_set.all():
            node_version = node.create_new_version()
            node_version.pipeline = pipeline_version
            node_version.save(update_fields=["pipeline"])

        return pipeline_version

    @transaction.atomic()
    def archive(self) -> bool:
        """
        Archive this record only when it is not still being referenced by other records. If this record is the working
        version, all of its versions will be archived as well. The same goes for its nodes.
        """
        if self.get_related_experiments_queryset().exists():
            return False

        if len(self.get_static_trigger_experiment_ids()) > 0:
            return False

        super().archive()
        for node in self.node_set.all():
            node.archive()

        if self.is_working_version:
            for version in self.versions.filter(is_archived=False):
                version.archive()

        return True

    def get_node_param_values(self, node_cls, param_name: str) -> list:
        return list(self.node_set.filter(type=node_cls.__name__).values_list(f"params__{param_name}", flat=True))

    def get_related_experiments_queryset(self) -> models.QuerySet:
        return self.experiment_set.filter(is_archived=False)

    def get_static_trigger_experiment_ids(self) -> models.QuerySet:
        from apps.events.models import EventAction, EventActionType

        return (
            EventAction.objects.filter(
                action_type=EventActionType.PIPELINE_START,
                params__pipeline_id=self.id,
                static_trigger__is_archived=False,
            )
            .annotate(trigger_experiment_id=models.F("static_trigger__experiment"))
            .values("trigger_experiment_id")
        )

    @property
    def version_details(self) -> VersionDetails:
        reserved_types = ["StartNode", "EndNode"]

        def node_name(node):
            name = node.params.get("name")
            if name == node.flow_id:
                return node.type
            return name

        return VersionDetails(
            instance=self,
            fields=[
                VersionField(name="name", raw_value=self.name),
                VersionField(
                    name="nodes",
                    queryset=self.node_set.exclude(type__in=reserved_types),
                    to_display=node_name,
                ),
            ],
        )


class Node(BaseModel, VersionsMixin, CustomActionOperationMixin):
    flow_id = models.CharField(max_length=128, db_index=True)  # The ID assigned by react-flow
    type = models.CharField(max_length=128)  # The node type, should be one from nodes/nodes.py
    label = models.CharField(max_length=128, blank=True, default="")  # The human readable label
    params = models.JSONField(default=dict)  # Parameters for the specific node type
    working_version = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="versions",
    )
    is_archived = models.BooleanField(default=False)
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE)
    objects = NodeObjectManager()

    def __str__(self):
        return self.flow_id

    def create_new_version(self):
        """
        Create a new version of the node and if the node is an assistant node, create a new version of the assistant
        and update the `assistant_id` in the node params to the new assistant version id.
        """
        from apps.assistants.models import OpenAiAssistant
        from apps.pipelines.nodes.nodes import AssistantNode

        assistant_node_name = AssistantNode.__name__

        new_version = super().create_new_version(save=False)

        if self.type == assistant_node_name and new_version.params.get("assistant_id"):
            assistant = OpenAiAssistant.objects.get(id=new_version.params.get("assistant_id"))
            if not assistant.is_a_version:
                assistant_version = assistant.create_new_version()
                # convert to string to be consistent with values from the UI
                new_version.params["assistant_id"] = str(assistant_version.id)

        new_version.save()
        self._copy_custom_action_operations_to_new_version(new_node=new_version)

        return new_version

    def update_from_params(self):
        """Callback to do DB related updates pertaining to the node params"""
        from apps.pipelines.nodes.nodes import LLMResponseWithPrompt

        if self.type == LLMResponseWithPrompt.__name__:
            custom_action_infos = []
            for custom_action_operation in self.params.get("custom_actions") or []:
                custom_action_id, operation_id = custom_action_operation.split(":")
                custom_action_infos.append({"custom_action_id": custom_action_id, "operation_id": operation_id})

            set_custom_actions(self, custom_action_infos)

    def archive(self):
        """
        Archiving a node will also archive the assistant if it is an assistant node. The node's versions will be
        archived when the pipeline they belong to is archived.
        """
        from apps.assistants.models import OpenAiAssistant

        super().archive()
        if self.is_a_version and self.type == "AssistantNode":
            assistant_id = self.params.get("assistant_id")
            if assistant_id:
                assistant = OpenAiAssistant.objects.get(id=assistant_id)
                assistant.archive()

    @property
    def version_details(self) -> VersionDetails:
        from apps.assistants.models import OpenAiAssistant
        from apps.experiments.models import VersionFieldDisplayFormatters

        node_name = self.params.get("name", self.type)
        if node_name == self.flow_id:
            node_name = self.type

        param_versions = []
        for name, value in self.params.items():
            display_formatter = None
            match name:
                case "tools":
                    display_formatter = VersionFieldDisplayFormatters.format_tools
                case "custom_actions":
                    display_formatter = VersionFieldDisplayFormatters.format_custom_action_operation
                case "name":
                    value = node_name
                case "assistant_id":
                    name = "assistant"
                    # Load the assistant, since it is being versioned
                    value = OpenAiAssistant.objects.filter(id=value).first()

            param_versions.append(
                VersionField(group_name=node_name, name=name, raw_value=value, to_display=display_formatter)
            )

        return VersionDetails(
            instance=self,
            fields=param_versions,
        )


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
    NODE = "node", "Node"
    NAMED = "named", "Named"
    GLOBAL = "global", "Global"
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
    node_id = models.TextField()
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
        """
        Converts this message instance into a list of Langchain `BaseMessage` objects.
        The message order is the reverse of the typical order because of where this
        method is called. The returned order is: [`AIMessage`, `HumanMessage`, `SystemMessage`].

        The `SystemMessage` represents the conversation summary and will only be
        included if it exists.
        """
        langchain_messages = [
            AIMessage(content=self.ai_message, additional_kwargs={"id": self.id, "node_id": self.node_id}),
            HumanMessage(content=self.human_message, additional_kwargs={"id": self.id, "node_id": self.node_id}),
        ]
        if self.summary:
            langchain_messages.append(
                SystemMessage(content=self.summary, additional_kwargs={"id": self.id, "node_id": self.node_id})
            )

        return langchain_messages
