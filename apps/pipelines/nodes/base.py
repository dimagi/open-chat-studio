import logging
import operator
from abc import ABC
from collections.abc import Callable, Sequence
from copy import deepcopy
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, cast

from langchain_core.runnables import RunnableConfig
from langgraph.constants import END
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.config import JsonDict
from pydantic.json_schema import SkipJsonSchema
from typing_extensions import TypedDict

from apps.experiments.models import ExperimentSession
from apps.pipelines.exceptions import PipelineNodeRunError
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


class Intents(StrEnum):
    """Intents capture actions which should be taken after the pipeline has run."""

    END_SESSION = "end_session"


class TempState(TypedDict):
    user_input: str
    outputs: dict
    attachments: list


class PipelineState(dict):
    messages: Annotated[Sequence[Any], operator.add]
    outputs: Annotated[dict, add_messages]
    experiment_session: ExperimentSession
    temp_state: Annotated[TempState, add_temp_state_messages]
    input_message_metadata: Annotated[dict, merge_dicts]
    output_message_metadata: Annotated[dict, merge_dicts]
    attachments: list = Field(default=[])
    output_message_tags: Annotated[list[str], operator.add]
    session_tags: Annotated[list[str], operator.add]

    # List of (previous, current, next) tuples used for aiding in routing decisions.
    path: Annotated[Sequence[tuple[str | None, str, list[str]]], operator.add]
    # input to the current node
    node_input: str
    # source node for the current node
    node_source: str

    intents: Annotated[list[Intents], operator.add]

    def json_safe(self):
        # We need to make a copy of `self` to not change the actual value of `experiment_session` forever
        copy = self.copy()
        if "experiment_session" in copy:
            copy["experiment_session"] = copy["experiment_session"].id

        if "attachments" in copy.get("temp_state", {}):
            copy["temp_state"]["attachments"] = [att.model_dump() for att in copy["temp_state"]["attachments"]]

        if interrupt := copy.pop("__interrupt__", None):
            copy["interrupt"] = interrupt[0].value
        return copy

    @classmethod
    def clone(cls, state):
        """Make a copy of the state."""
        copied = state.copy()
        # Don't deepcopy Django models
        session = copied.pop("experiment_session")
        copied = deepcopy(copied)
        copied["experiment_session"] = session
        return PipelineState(copied)

    @classmethod
    def from_node_output(cls, node_name: str, node_id: str, output: Any = None, **kwargs) -> Self:
        kwargs["outputs"] = {node_name: {"message": output, "node_id": node_id}}
        kwargs.setdefault("temp_state", {}).update({"outputs": {node_name: output}})
        if output is not None:
            kwargs["messages"] = [output]

        return cls(**kwargs)

    def add_message_tag(self, tag: str):
        self.setdefault("output_message_tags", []).append((tag, None))

    def add_session_tag(self, tag: str):
        self.setdefault("session_tags", []).append((tag, None))

    def get_node_id(self, node_name: str):
        """
        Helper method to get a node ID from a node name.
        """
        return self.get("outputs", {}).get(node_name, {}).get("node_id")

    def get_node_name(self, node_id: str):
        """
        Helper method to get a node name from a node ID.
        """
        for name, output in self.get("outputs", {}).items():
            if output.get("node_id") == node_id:
                return name
        return None

    def get_selected_route(self, node_name: str) -> str | None:
        """
        Gets the route selected by a specific router node.
        """
        outputs = self.get("outputs", {})
        if node_name in outputs and "route" in outputs[node_name]:
            return outputs[node_name].get("route")

        return None

    def get_node_path(self, node_name: str) -> list | None:
        """
        Gets the path (list of node names) leading to the specified node.
        Returns:
            A list containing the sequence of nodes leading to the target node.
            If the node is not found in the pipeline path, returns a list containing
            only the specified node name.
        """
        path = []
        current_name = node_name
        while current_name:
            path.insert(0, current_name)
            current_node_id = self.get_node_id(current_name)
            if not current_node_id:
                break

            for _, current, targets in self.get("path", []):
                if current_node_id in targets:
                    current_name = self.get_node_name(current)
                    break
            else:
                break

        return path

    def get_execution_flow(self):
        """Returns the execution flow of the pipeline as a list of tuples.
        Each tuple contains the previous node name, the current node name, and a list of destination node names.
        """
        return [
            (self.get_node_name(prev), self.get_node_name(source), [self.get_node_name(x) for x in dest])
            for prev, source, dest in self.get("path", [])
        ]

    def get_all_routes(self) -> dict:
        """
        Gets all routing decisions in the pipeline.
        """
        routes_dict = {}
        outputs = self.get("outputs", {})
        for node_name, node_data in outputs.items():
            if "route" in node_data:
                routes_dict[node_name] = node_data["route"]

        return routes_dict

    def get_node_output_by_name(self, node_name: str) -> Any:
        """
        Get the output of a node by its name.
        """
        output = self["outputs"].get(node_name)
        if output:
            return output["message"]
        return None

    def get_node_output(self, node_id: str) -> Any:
        """
        Get the output of a node by its ID.
        """
        for output in self["outputs"].values():
            if output.get("node_id") == node_id:
                return output["message"]
        return None

    def get_node_inputs(self, node_id: str, incoming_nodes: list[str]) -> dict[str, Any]:
        """
        Get the inputs for the given node based on the node's incoming edges.

        Returns:
            A dictionary mapping incoming node IDs to their respective outputs in the state.
            If there is no output for an incoming edge or if that edge was not targeted,
            the value in the output will be None.
        """
        inputs = {}
        for incoming_node_id in incoming_nodes:
            targets = [step[2] for step in self["path"] if step[1] == incoming_node_id]
            if targets and node_id in targets[0]:
                # only include outputs from nodes that targeted the current node
                inputs[incoming_node_id] = self.get_node_output(incoming_node_id)
            else:
                inputs[incoming_node_id] = None
        return inputs

    @classmethod
    def from_router_output(
        cls, node_id, node_name, output, output_handle, tags, route_path, conditional_branch
    ) -> Self:
        return cls(
            outputs={
                node_name: {
                    "node_id": node_id,
                    "output_handle": output_handle,
                    "route": conditional_branch,
                    "message": output,
                },
            },
            temp_state={"outputs": {node_name: output}},
            output_message_tags=tags,
            path=[route_path],
        )


class BasePipelineNode(BaseModel, ABC):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    _config: RunnableConfig = None
    _incoming_nodes: list[str] = None
    _outgoing_nodes: list[str] = None

    node_id: SkipJsonSchema[str] = Field(exclude=True)
    django_node: SkipJsonSchema[Any] = Field(exclude=True)

    name: str = Field(title="Node Name", json_schema_extra={"ui:widget": "node_name"})

    def _prepare_state(self, node_id: str, incoming_nodes: list, state: PipelineState):
        """This function initializes the state before executing the node function. This is primarily
        determining which output to select from the state as this node's input.
        """
        from apps.channels.datamodels import Attachment

        if not incoming_nodes:
            # This is the first node in the graph
            state["node_input"] = state["messages"][-1]
            state["node_source"] = None

            # init temp state here to avoid having to do it in each place the pipeline is invoked
            state["temp_state"]["user_input"] = state["node_input"]
            state["temp_state"]["attachments"] = [
                Attachment.model_validate(att) for att in state.get("attachments", [])
            ]
        else:
            for incoming_node_id, output in reversed(state.get_node_inputs(node_id, incoming_nodes).items()):
                if output is not None:
                    state["node_input"] = output
                    state["node_source"] = incoming_node_id
                    break
            else:
                raise PipelineNodeRunError(
                    f"Cannot determine which input to use for node {node_id}",
                    {
                        "node_name": self.name,
                        "node_id": node_id,
                        "incoming_node_ids": incoming_nodes,
                        "state_outputs": state["outputs"],
                        "pipeline_path": state["path"],
                    },
                )
        return state

    def get_participant_data_proxy(self, state: PipelineState) -> "ParticipantDataProxy":
        return ParticipantDataProxy.from_state(state)

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
        self, incoming_nodes: list, outgoing_nodes: list, state: PipelineState, config: RunnableConfig
    ) -> PipelineState | Command:
        self._config = config
        self._incoming_nodes = incoming_nodes
        self._outgoing_nodes = outgoing_nodes
        state = PipelineState(state)
        state = self._prepare_state(self.node_id, incoming_nodes, state)
        output = self._process(input=state["node_input"], state=state)
        if isinstance(output, Command) and output.goto != END:
            return Command(goto=output.goto, update=self._augment_output(state, cast(PipelineState, output.update)))
        if not isinstance(output, dict):
            return output
        return self._augment_output(state, output)

    def _augment_output(self, state, output: PipelineState) -> PipelineState:
        output["path"] = [(state["node_source"], self.node_id, self._outgoing_nodes)]
        get_output_tags_fn = getattr(self, "get_output_tags", None)
        output.setdefault("output_message_tags", [])
        if callable(get_output_tags_fn):
            output["output_message_tags"].extend(get_output_tags_fn())
        return output

    def _process(self, input: str, state: PipelineState) -> PipelineState | Command:
        """The method that executes node specific functionality"""
        raise NotImplementedError


class PipelineRouterNode(BasePipelineNode):
    def build_router_function(
        self, edge_map: dict, incoming_edges: list
    ) -> Callable[[PipelineState, RunnableConfig], Command]:
        output_map = self.get_output_map()
        ReturnType = Command[Literal[tuple(edge_map.values())]]  # noqa

        def router(state: PipelineState, config: RunnableConfig) -> ReturnType:
            self._config = config

            state = PipelineState(state)
            state = self._prepare_state(self.node_id, incoming_edges, state)

            conditional_branch, is_default_keyword = self._process_conditional(state)
            output_handle = next((k for k, v in output_map.items() if v == conditional_branch), None)
            tags = self.get_output_tags(conditional_branch, is_default_keyword)
            # edge map won't contain the conditional branch if that handle isn't connected to another node
            target_node_id = edge_map.get(conditional_branch)
            route_path = (state["node_source"], self.node_id, [target_node_id] if target_node_id else [])
            output = PipelineState.from_router_output(
                self.node_id, self.name, state["node_input"], output_handle, tags, route_path, conditional_branch
            )
            return Command(
                update=output,
                goto=[target_node_id] if target_node_id else [],
            )

        return router

    def get_output_map(self) -> dict[str, str]:
        raise NotImplementedError()

    def get_output_tags(self, selected_route, is_default_keyword) -> list[str]:
        raise NotImplementedError()

    def _process_conditional(self, state: PipelineState):
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
    built_in_tools = "built_in_tools"
    key_value_pairs = "key_value_pairs"
    text_editor = "text_editor_widget"


class OptionsSource(StrEnum):
    source_material = "source_material"
    assistant = "assistant"
    agent_tools = "agent_tools"
    custom_actions = "custom_actions"
    collection = "collection"
    built_in_tools = "built_in_tools"
    collection_index = "collection_index"
    built_in_tools_config = "built_in_tools_config"
    text_editor_autocomplete_vars_llm_node = "text_editor_autocomplete_vars_llm_node"
    text_editor_autocomplete_vars_router_node = "text_editor_autocomplete_vars_router_node"


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
    icon: str = None
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
        if self.icon:
            schema["ui:icon"] = self.icon


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
