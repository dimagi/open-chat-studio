import operator
from abc import ABC
from collections.abc import Sequence
from functools import cached_property
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ConfigDict

from apps.experiments.models import ExperimentSession
from apps.pipelines.logging import PipelineLoggingCallbackHandler


def add_messages(left: dict, right: dict):
    return {**left, **right}
    # return left + right


class PipelineState(dict):
    messages: Annotated[Sequence[Any], operator.add]
    outputs: Annotated[dict, add_messages]
    experiment_session: ExperimentSession

    def json_safe(self):
        # We need to make a copy of `self` so as to not change the actual value of `experiment_session` forever
        copy = self.copy()
        if "experiment_session" in copy:
            copy["experiment_session"] = copy["experiment_session"].id
        return copy


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
        if incoming_edges:
            # TODO: what to do if the node is a "combination"?
            # Wait for all inputs? I don't think we can do that...
            # Assume there is only a single path that we care about? (e.g. in a router)
            previous_node_id = incoming_edges[0]
            input = state["outputs"][previous_node_id]
        else:  # This is the input node
            input = state["messages"][-1]
        output = self._process(input, state)
        # Append the output to the state, otherwise do not change the state
        return PipelineState(messages=[output], outputs={node_id: output}) if output else PipelineState()

    def _process(self, input, state: PipelineState) -> PipelineState:
        """The method that executes node specific functionality"""
        raise NotImplementedError

    @cached_property
    def logger(self):
        for handler in self._config["callbacks"].handlers:
            if isinstance(handler, PipelineLoggingCallbackHandler):
                return handler.logger
