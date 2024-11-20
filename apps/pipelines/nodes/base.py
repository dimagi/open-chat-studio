import operator
from abc import ABC
from collections.abc import Sequence
from enum import StrEnum
from functools import cached_property
from typing import Annotated, Any, Self

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict
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
    def from_node_output(cls, node_id: str, output: any = None, **kwargs) -> Self:
        kwargs["outputs"] = {node_id: output}
        if output is not None:
            kwargs["messages"] = [output]
        return PipelineState(**kwargs)


class PipelineNode(BaseModel, ABC):
    """Pipeline node that implements the `_process` method and returns a new state. Define required parameters as
    typed fields.

    Example:
        class FunNode(PipelineNode):
            required_parameter_1: CustomType
            optional_parameter_1: Optional[CustomType] = None

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

    def process(self, node_id: str, incoming_edges: list, state: PipelineState, config) -> PipelineState:
        self._config = config

        for incoming_edge in reversed(incoming_edges):
            # We assume there is only a single path that would be valid through
            # the graph.
            # If we wanted to have multiple parallel paths that end
            # in a single node, we should give that node multiple inputs, and
            # read the input from that particular input
            if incoming_edge in state["outputs"]:
                input = str(state["outputs"][incoming_edge])
                break
        else:  # This is the first node in the graph
            input = state["messages"][-1]
        return self._process(input=input, state=state, node_id=node_id)

    def _process(self, input: str, state: PipelineState, node_id: str) -> str:
        """The method that executes node specific functionality"""
        raise NotImplementedError

    @cached_property
    def logger(self):
        for handler in self._config["callbacks"].handlers:
            if isinstance(handler, PipelineLoggingCallbackHandler):
                return handler.logger


class Widgets(StrEnum):
    expandable_text = "expandable_text"
    toggle = "toggle"
    select = "select"
    float = "float"
    none = "none"

    # special widgets
    llm_provider_model = "llm_provider_model"
    history = "history"
    keywords = "keywords"


class OptionsSource(StrEnum):
    source_material = "source_material"
    assistant = "assistant"


class UiSchema(BaseModel):
    widget: Widgets = None
    enum_labels: list[str] = None
    options_source: str = None

    def __call__(self, schema: JsonDict):
        if self.widget:
            schema["ui:widget"] = self.widget
        if self.enum_labels:
            schema["ui:enumLabels"] = self.enum_labels
        if self.options_source:
            schema["ui:optionsSource"] = self.options_source


class NodeSchema(BaseModel):
    label: str

    def __call__(self, schema: JsonDict):
        schema["ui:label"] = self.label
