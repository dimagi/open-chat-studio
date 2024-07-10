from abc import ABC
from collections.abc import Callable, Sequence
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel
from pydantic_core import ValidationError

from apps.experiments.models import ExperimentSession
from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.graph import Node
from apps.pipelines.logging import PipelineLoggingCallbackHandler


def add_messages(left: list, right: list):
    # Could probably log here
    return left + right


class PipelineState(TypedDict):
    messages: Annotated[Sequence[Any], add_messages]
    experiment_session_id: int


class PipelineNode(BaseModel, ABC):
    """Pipeline node that is either a single or a combination of Langchain Runnables

    Define required parameters as typed fields. Compose the runnable in `get_runnable`

    Example:
        class FunNode(PipelineNode):
            required_parameter_1: CustomType
            optional_parameter_1: Optional[CustomType] = None

            def get_runnable(self, input) -> str:
                if self.optional_parameter_1:
                    return PromptTemplate.from_template(template=self.required_parameter_1)
                else:
                    return LLMResponse.build(node)

       class FunLambdaNode(PipelineNode):
            required_parameter_1: str

            def get_runnable(self, node: Node):
                def fn(input: Input):
                    return self.required_parameter_1
                return RunnableLambda(fn, name=self.__class__.__name__)
    """

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def build(cls, node: Node) -> Callable[[dict], dict]:
        try:
            return cls(**node.params)
        except ValidationError as ex:
            raise PipelineNodeBuildError(ex)

    def process(self, state: PipelineState) -> PipelineState:
        """The method that executes node specific functionality"""
        raise NotImplementedError

    def logger(self, config):
        for handler in config["callbacks"].handlers:
            if isinstance(handler, PipelineLoggingCallbackHandler):
                return handler.logger
        raise AttributeError("No logger found")

    def experiment_session(self, state: PipelineState) -> ExperimentSession:
        return ExperimentSession.objects.get(id=state["experiment_session_id"])
