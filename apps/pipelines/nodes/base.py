import logging
import operator
from abc import ABC
from collections.abc import Callable, Sequence
from copy import deepcopy
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, cast

import sentry_sdk
from langchain_core.runnables import RunnableConfig
from langgraph.constants import END
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.config import JsonDict
from pydantic.json_schema import SkipJsonSchema
from typing_extensions import TypedDict

from apps.experiments.models import ExperimentSession
from apps.generics.help import render_help_with_link
from apps.pipelines.exceptions import PipelineNodeRunError

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


def merge_dict_values_as_lists(left: dict, right: dict):
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

    # Outputs from nodes that have already been executed.
    # Dictionary keyed by node name. Values may be a string or a list of strings if the
    # node was executed more than once.
    outputs: Annotated[dict, add_messages]
    experiment_session: ExperimentSession
    temp_state: Annotated[TempState, add_temp_state_messages]
    input_message_metadata: Annotated[dict, merge_dict_values_as_lists]
    output_message_metadata: Annotated[dict, merge_dict_values_as_lists]
    attachments: list = Field(default=[])
    output_message_tags: Annotated[list[tuple[str, str]], operator.add]
    session_tags: Annotated[list[tuple[str, str]], operator.add]

    # List of (previous, current, next) tuples used for aiding in routing decisions.
    path: Annotated[Sequence[tuple[str | None, str, list[str]]], operator.add]

    # inputs to the current node
    node_inputs: list[str]

    # input from the last executed node prior to this one
    last_node_input: str

    # source node for the current node
    node_source: str

    intents: Annotated[list[Intents], operator.add]
    synthetic_voice_id: int | None

    participant_data: Annotated[dict, operator.or_]
    session_state: Annotated[dict, operator.or_]

    input_message_id: int | None
    input_message_url: str | None

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
    def from_node_output(
        cls,
        node_name: str,
        node_id: str,
        output: Any = None,
        **kwargs,
    ) -> Self:
        kwargs["outputs"] = {node_name: {"message": output, "node_id": node_id}}
        kwargs.setdefault("temp_state", {}).update({"outputs": {node_name: output}})
        if output is not None:
            kwargs["messages"] = [output]
        return cls(**kwargs)

    def add_message_tag(self, tag: str):
        """Adds a tag to the output message."""
        self.setdefault("output_message_tags", []).append((tag, ""))

    def add_session_tag(self, tag: str):
        """Adds the tag to the chat session."""
        self.setdefault("session_tags", []).append((tag, ""))

    def get_node_id(self, node_name: str) -> str | None:
        """
        Helper method to get a node ID from a node name.
        """
        outputs = self.get_node_outputs_by_name(node_name)
        return outputs[-1]["node_id"] if outputs else None

    def get_node_name(self, node_id: str) -> str | None:
        """
        Helper method to get a node name from a node ID.
        """
        for name, output in self.get("outputs", {}).items():
            if isinstance(output, list):
                output = output[0] if output else None
            if output and output.get("node_id") == node_id:
                return name
        return None

    def get_selected_route(self, node_name: str) -> str | None:
        """
        Returns the route keyword selected by a specific router node with the given name.
        If the node does not exist or has no route defined, it returns `None`.
        """
        outputs = self.get_node_outputs_by_name(node_name)
        return outputs[-1].get("route") if outputs else None

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
        Returns a dictionary containing all routing decisions made in the pipeline up to the current node.
        The keys are the node names and the values are the route keywords chosen by each router node.

        Note that in parallel workflows only the most recent route for a particular node will be returned.
        """
        routes_dict = {}
        outputs = self.get("outputs", {})
        for node_name, node_data in outputs.items():
            if isinstance(node_data, list):
                # Unclear how to handle the case where a router gets called twice due to parallel execution
                # Take the last one for now
                node_data = node_data[-1]
            if "route" in node_data:
                routes_dict[node_name] = node_data["route"]

        return routes_dict

    def get_node_output_by_name(self, node_name: str) -> Any:
        """
        Returns the output of the specified node if it has been executed.
        If the node has not been executed, it returns `None`.
        """
        outputs = self.get_node_outputs_by_name(node_name)
        return outputs[-1]["message"] if outputs else None

    def get_node_outputs_by_name(self, node_name: str) -> list[dict] | None:
        """
        Get the outputs of a node by its name.
        """
        outputs = self["outputs"].get(node_name)
        if outputs is not None:
            return outputs if isinstance(outputs, list) else [outputs]
        return None

    def get_node_outputs(self, node_id: str) -> list[str] | None:
        """
        Get the outputs of a node by its ID.
        """
        for outputs in self["outputs"].values():
            output = outputs if isinstance(outputs, list) else [outputs]
            if output and output[0].get("node_id") == node_id:
                return [out["message"] for out in output]
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
                inputs[incoming_node_id] = self.get_node_outputs(incoming_node_id)
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
            state["last_node_input"] = state["messages"][-1]
            state["node_inputs"] = [state["messages"][-1]]
            state["node_source"] = None

            # init temp state here to avoid having to do it in each place the pipeline is invoked
            state["temp_state"]["user_input"] = state["last_node_input"]
            state["temp_state"]["attachments"] = [
                Attachment.model_validate(att) for att in state.get("attachments", [])
            ]
        else:
            for incoming_node_id, outputs in reversed(state.get_node_inputs(node_id, incoming_nodes).items()):
                if outputs is not None:
                    # Handle the edge case where a node is downstream of a 'join' node connected to
                    # multiple parallel nodes. This isn't really a supported workflow, and it's hard to detect
                    # in the pipeline during the build step.
                    # By taking the last message, we at least get different outputs for each invocation of
                    # the node in the case where the parallel branches are of different lengths.
                    state["last_node_input"] = outputs[-1]
                    state["node_inputs"] = outputs
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

        # Sentry context for error tracking
        process_params = {"state": state}
        sentry_context = {
            "node_id": self.node_id,
            "node_name": self.name,
            "node_type": self.__class__.__name__,
            "params": process_params,
        }
        sentry_sdk.set_context("Node", sentry_context)

        output = self._process(**process_params)
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

    def _process(self, state: PipelineState) -> PipelineState | Command:
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
                self.node_id, self.name, state["last_node_input"], output_handle, tags, route_path, conditional_branch
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
    searchable_multiselect = "searchable_multiselect"
    none = "none"

    # special widgets
    llm_provider_model = "llm_provider_model"
    history = "history"
    keywords = "keywords"
    history_mode = "history_mode"
    built_in_tools = "built_in_tools"
    key_value_pairs = "key_value_pairs"
    text_editor = "text_editor_widget"
    voice_widget = "voice_widget"


class OptionsSource(StrEnum):
    source_material = "source_material"
    assistant = "assistant"
    agent_tools = "agent_tools"
    custom_actions = "custom_actions"
    collection = "collection"
    built_in_tools = "built_in_tools"
    mcp_tools = "mcp_tools"
    collection_index = "collection_index"
    built_in_tools_config = "built_in_tools_config"
    text_editor_autocomplete_vars_llm_node = "text_editor_autocomplete_vars_llm_node"
    text_editor_autocomplete_vars_router_node = "text_editor_autocomplete_vars_router_node"
    voice_provider_id = "voice_provider_id"
    synthetic_voice_id = "synthetic_voice_id"


class VisibleWhen(BaseModel):
    """Defines a condition under which a field should be visible in the UI.

    Supported operators: "==", "!=", "in", "not_in", "is_empty", "is_not_empty"
    """

    field: str
    value: Any = None
    operator: Literal["==", "!=", "in", "not_in", "is_empty", "is_not_empty"] = "=="


class UiSchema(BaseModel):
    widget: Widgets = None

    # Use this with Enum fields to provide label text
    enum_labels: list[str] = None

    # Use this with 'select' type fields to indicate where the options should come from
    # See `apps.pipelines.views._pipeline_node_parameter_values`
    options_source: OptionsSource = None
    flag_required: str = None

    # Use this to conditionally show/hide a field based on another field's value.
    # Can be a single condition or a list of conditions (all must be satisfied).
    visible_when: VisibleWhen | list[VisibleWhen] | None = None

    def __call__(self, schema: JsonDict):
        if self.widget:
            schema["ui:widget"] = self.widget
        if self.enum_labels:
            schema["ui:enumLabels"] = self.enum_labels
        if self.options_source:
            schema["ui:optionsSource"] = self.options_source
        if self.flag_required:
            schema["ui:flagRequired"] = self.flag_required
        if self.visible_when is not None:
            if isinstance(self.visible_when, list):
                schema["ui:visibleWhen"] = [cond.model_dump() for cond in self.visible_when]
            else:
                schema["ui:visibleWhen"] = self.visible_when.model_dump()


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


def deprecated_node(cls=None, *, message=None, docs_link=None):
    """Class decorator for deprecating a node"""

    def _inner(cls):
        notice = message or ""
        if docs_link:
            notice = render_help_with_link(notice, docs_link)
        schema = cls.model_config["json_schema_extra"]
        schema.deprecated = True
        schema.deprecation_message = notice
        schema.can_add = False
        return cls

    if cls is None:
        return _inner

    return _inner(cls)
