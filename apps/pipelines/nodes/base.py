import operator
from abc import ABC
from collections.abc import Sequence
from enum import StrEnum
from functools import cached_property
from typing import Annotated, Any, Literal, Self

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.config import JsonDict

from apps.experiments.models import ExperimentSession
from apps.pipelines.logging import PipelineLoggingCallbackHandler


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


class PipelineState(dict):
    messages: Annotated[Sequence[Any], operator.add]
    outputs: Annotated[dict, add_messages]
    experiment_session: ExperimentSession
    pipeline_version: int
    ai_message_id: int | None = None
    message_metadata: dict | None = None
    attachments: list | None = None

    def json_safe(self):
        # We need to make a copy of `self` so as to not change the actual value of `experiment_session` forever
        copy = self.copy()
        if "experiment_session" in copy:
            copy["experiment_session"] = copy["experiment_session"].id
        return copy

    @classmethod
    def from_node_output(cls, node_id: str, output: Any = None, **kwargs) -> Self:
        kwargs["outputs"] = {node_id: {"message": output}}
        if output is not None:
            kwargs["messages"] = [output]
        return cls(**kwargs)


class PipelineNode(BaseModel, ABC):
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

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _config: RunnableConfig | None = None
    name: str = Field(
        description="The node name",
    )

    def process(self, node_id: str, incoming_edges: list, state: PipelineState, config) -> PipelineState:
        self._config = config

        for incoming_edge in reversed(incoming_edges):
            # We assume there is only a single path that would be valid through
            # the graph.
            # If we wanted to have multiple parallel paths that end
            # in a single node, we should give that node multiple inputs, and
            # read the input from that particular input
            if incoming_edge in state["outputs"]:
                input = state["outputs"][incoming_edge]["message"]
                break
        else:  # This is the first node in the graph
            input = state["messages"][-1]
        return self._process(input=input, state=state, node_id=node_id)

    def process_conditional(self, state: PipelineState, node_id: str | None = None) -> str:
        conditional_branch = self._process_conditional(state, node_id)
        output_map = self.get_output_map()
        output_handle = next((k for k, v in output_map.items() if v == conditional_branch), None)
        state["outputs"][node_id]["output_handle"] = output_handle
        return conditional_branch

    def _process(self, input: str, state: PipelineState, node_id: str) -> str:
        """The method that executes node specific functionality"""
        raise NotImplementedError

    def _process_conditional(self, state: PipelineState, node_id: str | None = None):
        """The method that selects which branch of a conditional node to go down."""
        raise NotImplementedError

    def get_output_map(self) -> dict:
        """A mapping from the output handles on the frontend to the return values of _process_conditional"""
        raise NotImplementedError

    @cached_property
    def logger(self):
        for handler in self._config["callbacks"].handlers:
            if isinstance(handler, PipelineLoggingCallbackHandler):
                return handler.logger


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


class OptionsSource(StrEnum):
    source_material = "source_material"
    assistant = "assistant"
    agent_tools = "agent_tools"
    custom_actions = "custom_actions"


class UiSchema(BaseModel):
    widget: Widgets = None

    # Use this with Enum fields to provide label text
    enum_labels: list[str] = None

    # Use this with 'select' type fields to indicate where the options should come from
    # See `apps.pipelines.views._pipeline_node_parameter_values`
    options_source: OptionsSource = None

    def __call__(self, schema: JsonDict):
        if self.widget:
            schema["ui:widget"] = self.widget
        if self.enum_labels:
            schema["ui:enumLabels"] = self.enum_labels
        if self.options_source:
            schema["ui:optionsSource"] = self.options_source


class NodeSchema(BaseModel):
    label: str
    flow_node_type: Literal["pipelineNode", "startNode", "endNode"] = "pipelineNode"
    can_delete: bool = None
    can_add: bool = None
    deprecated: bool = False
    deprecation_message: str = None

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
