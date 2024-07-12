from abc import ABC
from collections.abc import Callable, Sequence
from functools import cached_property
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel
from pydantic_core import ValidationError

from apps.experiments.models import ExperimentSession
from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.graph import Node
from apps.pipelines.logging import PipelineLoggingCallbackHandler


def add_messages(left: list, right: list):
    # Could probably log here
    return left + right


class PipelineState(dict):
    messages: Annotated[Sequence[Any], add_messages]
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

    _config: RunnableConfig | None = None

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def build(cls, node: Node) -> Callable[[dict], dict]:
        try:
            return cls(**node.params)
        except ValidationError as ex:
            raise PipelineNodeBuildError(ex)

    def process(self, state: PipelineState, config) -> PipelineState:
        self._config = config
        output = self._process(state)
        # Append the output to the state, otherwise do not change the state
        return PipelineState(messages=[output]) if output else PipelineState()

    def _process(self, state: PipelineState) -> PipelineState:
        """The method that executes node specific functionality"""
        raise NotImplementedError

    @cached_property
    def logger(self):
        for handler in self._config["callbacks"].handlers:
            if isinstance(handler, PipelineLoggingCallbackHandler):
                return handler.logger
