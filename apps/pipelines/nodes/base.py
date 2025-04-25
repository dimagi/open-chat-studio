import logging
import operator
from abc import ABC
from collections.abc import Callable, Sequence
from enum import StrEnum
from functools import cached_property
from typing import Annotated, Any, Literal, Self

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.config import JsonDict
from typing_extensions import TypedDict

from apps.experiments.models import ExperimentSession
from apps.pipelines.exceptions import PipelineNodeRunError
from apps.pipelines.logging import LoggingCallbackHandler, noop_logger
from apps.service_providers.llm_service.prompt_context import ParticipantDataProxy

logger = logging.getLogger("ocs.pipelines")


def add_messages(left: dict, right: dict):
    # If the node already has an output, create a list and append the value to it
    output = {**left}
    for key, value in right.items():
        if key in output:
            if isinstance(output[key], list):
                output[key] = [*output[key], value]
            else:
                output[key] = [output[key], value]
        else:
            output[key] = value
    return output


def add_temp_state_messages(left: dict, right: dict):
    output = {**left}
    try:
        output["outputs"].update(right["outputs"])
    except KeyError:
        output["outputs"] = right.get("outputs", {})
    for key, value in right.items():
        if key != "outputs":
            output[key] = value

    return output


def merge_dicts(left: dict, right: dict):
    """
    Merge two dictionaries, combining values for the same key into a list. The value of any key is expected to be a list
    """
    output = {**left}
    for key, value in right.items():
        if key in output:
            if isinstance(output[key], list):
                output[key] = list(set(output[key]) | set(value))
            elif isinstance(output[key], bool):
                output[key] = value
            else:
                output[key] = [output[key], value]
        else:
            output[key] = value
    return output


class TempState(TypedDict):
    user_input: str
    outputs: dict
    attachments: list


class PipelineState(dict):
    messages: Annotated[Sequence[Any], operator.add]
    outputs: Annotated[dict, add_messages]
    experiment_session: ExperimentSession
    pipeline_version: int
    temp_state: Annotated[TempState, add_temp_state_messages]
    input_message_metadata: Annotated[dict, merge_dicts]
    output_message_metadata: Annotated[dict, merge_dicts]
    attachments: list = Field(default=[])
    output_message_tags: Annotated[list[str], operator.add]

    # List of (previous, current, next) tuples used for aiding in routing decisions.
    path: Annotated[Sequence[tuple[str | None, str, list[str]]], operator.add]
    # input to the current node
    node_input: str
    # source node for the current node
    node_source: str

    def json_safe(self):
        # We need to make a copy of `self` to not change the actual value of `experiment_session` forever
        copy = self.copy()
        if "experiment_session" in copy:
            copy["experiment_session"] = copy["experiment_session"].id

        if "attachments" in copy.get("temp_state", {}):
            copy["temp_state"]["attachments"] = [att.model_dump() for att in copy["temp_state"]["attachments"]]
        return copy

    @classmethod
    def from_node_output(cls, node_name: str, node_id: str, output: Any = None, **kwargs) -> Self:
        kwargs["outputs"] = {node_id: {"message": output}}
        kwargs["temp_state"] = {"outputs": {node_name: output}}
        if output is not None:
            kwargs["messages"] = [output]

        return cls(**kwargs)

    @classmethod
    def from_router_output(cls, node_id, node_name, output, output_handle, tags, route_path) -> Self:
        return cls(
            outputs={node_id: {"output_handle": output_handle, "message": output}},
            temp_state={"outputs": {node_name: output}},
            output_message_tags=tags,
            path=[route_path],
        )


class BasePipelineNode(BaseModel, ABC):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    _config: RunnableConfig | None = None
    name: str = Field(title="Node Name", json_schema_extra={"ui:widget": "node_name"})

    def _prepare_state(self, node_id: str, incoming_edges: list, state: PipelineState):
        """This function initializes the state before executing the node function. This is primarily
        determining which output to select from the state as this node's input.
        """
        from apps.channels.datamodels import Attachment

        if not incoming_edges:
            # This is the first node in the graph
            state["node_input"] = state["messages"][-1]
            state["node_source"] = None

            # init temp state here to avoid having to do it in each place the pipeline is invoked
            state["temp_state"]["user_input"] = state["node_input"]
            state["temp_state"]["attachments"] = [
                Attachment.model_validate(att) for att in state.get("attachments", [])
            ]
        elif len(incoming_edges) == 1:
            incoming_edge = incoming_edges[0]
            state["node_input"] = state["outputs"][incoming_edge]["message"]
            state["node_source"] = incoming_edge
        else:
            # state.path is a list of tuples (previous, current, next)
            for path in reversed(state["path"]):
                candidate_node_ids = path[2]
                for candidate_node_id in candidate_node_ids:
                    if candidate_node_id == node_id:
                        previous_node_id = path[1]
                        state["node_input"] = state["outputs"][previous_node_id]["message"]
                        state["node_source"] = previous_node_id
                        break
                else:
                    continue
                break
            else:
                # This shouldn't happen, but keeping it here for now to avoid breaking
                logger.warning(f"Cannot determine which input to use for node {node_id}. Switching to fallback.")
                for incoming_edge in reversed(incoming_edges):
                    if incoming_edge in state["outputs"]:
                        node_input = state["outputs"][incoming_edge]["message"]
                        state["node_input"] = node_input
                        state["node_source"] = incoming_edge
                        break
                else:
                    raise PipelineNodeRunError(
                        f"Cannot determine which input to use for node {node_id}",
                        {
                            "node_id": node_id,
                            "edge_ids": incoming_edges,
                            "state_outputs": state["outputs"],
                        },
                    )
        return state

    def get_participant_data_proxy(self, state: PipelineState) -> "ParticipantDataProxy":
        return ParticipantDataProxy.from_state(state)

    @cached_property
    def logger(self):
        if not self._config or not self._config.get("callbacks"):
            return noop_logger()[0]

        for handler in self._config["callbacks"].handlers:
            if isinstance(handler, LoggingCallbackHandler):
                return handler.logger
        return noop_logger()[0]

    @property
    def disabled_tools(self) -> set[str] | None:
        if disabled := self._config.get("configurable", {}).get("disabled_tools"):
            return set(disabled)
        return None


class PipelineNode(BasePipelineNode, ABC):
    """Pipeline node that implements the `_process` method and returns a new state. Define required parameters as
    typed fields.

    Example:
        class FunNode(PipelineNode):
            required_parameter_1: str
            optional_parameter_1: int | None = None

            def _process(self, state: PipelineState) -> PipelineState:
                input = state["messages"][-1]
                output = ... # do something
                return output # The state will be updated with output

       class FunLambdaNode(PipelineNode):
            required_parameter_1: str

            def _process(self, state: PipelineState) -> PipelineState:
                ...
                return # The state will not be updated, since None is returned

    """

    def process(
        self, node_id: str, incoming_edges: list, outgoing_edges: list, state: PipelineState, config: RunnableConfig
    ) -> PipelineState:
        self._config = config
        state = self._prepare_state(node_id, incoming_edges, state)
        output = self._process(input=state["node_input"], state=state, node_id=node_id)
        output["path"] = [(state["node_source"], node_id, outgoing_edges)]
        return output

    def _process(self, input: str, state: PipelineState, node_id: str) -> PipelineState:
        """The method that executes node specific functionality"""
        raise NotImplementedError


class PipelineRouterNode(BasePipelineNode):
    def build_router_function(
        self, node_id: str, edge_map: dict, incoming_edges: list
    ) -> Callable[[PipelineState, RunnableConfig], Command]:
        output_map = self.get_output_map()
        ReturnType = Command[Literal[tuple(edge_map.values())]]  # noqa

        def router(state: PipelineState, config: RunnableConfig) -> ReturnType:
            self._config = config

            state = self._prepare_state(node_id, incoming_edges, state)

            conditional_branch = self._process_conditional(state, node_id)
            output_handle = next((k for k, v in output_map.items() if v == conditional_branch), None)
            tags = self.get_output_tags(conditional_branch)
            target_node_id = edge_map[conditional_branch]
            route_path = (state["node_source"], node_id, [target_node_id])
            output = PipelineState.from_router_output(
                node_id, self.name, state["node_input"], output_handle, tags, route_path
            )
            return Command(
                update=output,
                goto=target_node_id,
            )

        return router

    def get_output_map(self) -> dict[str, str]:
        raise NotImplementedError()

    def get_output_tags(self, selected_route) -> list[str]:
        raise NotImplementedError()

    def _process_conditional(self, state: PipelineState, node_id: str):
        raise NotImplementedError()


class Widgets(StrEnum):
    expandable_text = "expandable_text"
    code = "code"
    toggle = "toggle"
    select = "select"
    float = "float"
    range = "range"
    multiselect = "multiselect"
    none = "none"

    # special widgets
    llm_provider_model = "llm_provider_model"
    history = "history"
    keywords = "keywords"
    history_mode = "history_mode"


class OptionsSource(StrEnum):
    source_material = "source_material"
    assistant = "assistant"
    agent_tools = "agent_tools"
    custom_actions = "custom_actions"
    collection = "collection"


class UiSchema(BaseModel):
    widget: Widgets = None

    # Use this with Enum fields to provide label text
    enum_labels: list[str] = None

    # Use this with 'select' type fields to indicate where the options should come from
    # See `apps.pipelines.views._pipeline_node_parameter_values`
    options_source: OptionsSource = None
    flag_required: str = None

    def __call__(self, schema: JsonDict):
        if self.widget:
            schema["ui:widget"] = self.widget
        if self.enum_labels:
            schema["ui:enumLabels"] = self.enum_labels
        if self.options_source:
            schema["ui:optionsSource"] = self.options_source
        if self.flag_required:
            schema["ui:flagRequired"] = self.flag_required


class NodeSchema(BaseModel):
    label: str
    flow_node_type: Literal["pipelineNode", "startNode", "endNode"] = "pipelineNode"
    can_delete: bool = None
    can_add: bool = None
    deprecated: bool = False
    deprecation_message: str = None
    documentation_link: str = None
    field_order: list[str] = Field(
        None,
        description=(
            "The order of the fields in the UI. "
            "Any field not in this list will be appended to the end. "
            "The 'name' field is always displayed first regardless of its position in this list."
        ),
    )

    @model_validator(mode="after")
    def update_metadata_fields(self) -> Self:
        is_pipeline_node = self.flow_node_type == "pipelineNode"
        if self.can_delete is None:
            self.can_delete = is_pipeline_node
        if self.can_add is None:
            self.can_add = is_pipeline_node

        if self.deprecated:
            self.can_add = False
        return self

    def __call__(self, schema: JsonDict):
        schema["ui:label"] = self.label
        schema["ui:flow_node_type"] = self.flow_node_type
        schema["ui:can_delete"] = self.can_delete
        schema["ui:can_add"] = self.can_add
        schema["ui:deprecated"] = self.deprecated
        if self.deprecated and self.deprecation_message:
            schema["ui:deprecation_message"] = self.deprecation_message
        if self.field_order:
            schema["ui:order"] = self.field_order
        if self.documentation_link:
            schema["ui:documentation_link"] = self.documentation_link


def deprecated_node(cls=None, *, message=None):
    """Class decorator for deprecating a node"""

    def _inner(cls):
        schema = cls.model_config["json_schema_extra"]
        schema.deprecated = True
        schema.deprecation_message = message
        schema.can_add = False
        return cls

    if cls is None:
        return _inner

    return _inner(cls)
