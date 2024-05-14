from abc import ABC

from langchain_core.runnables import (
    Runnable,
    RunnableLambda,
)
from langchain_core.runnables.utils import Input
from pydantic import BaseModel
from pydantic_core import ValidationError

from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.graph import Node


class PipelineLambdaNode(BaseModel, ABC):
    """Pipeline node to run an arbitrary function.

    Define required parameters as typed fields. The function should be defined
    in _invoke(self, input). The function will be bound to an instance of
    PipelineLambdaNode, so will have access to all the fields.

    Example:
        class FunNode(PipelineLambdaNode):
            required_parameter_1: CustomType
            optional_parameter_1: Optional[CustomType] = None

            def _invoke(self, input) -> str:
                if self.optional_parameter_1:
                    return f"{self.required_parameter_1} is better than {input}"

    """

    class Config:
        arbitrary_types_allowed = True

    def _invoke(self, input: Input):
        """Define an arbitrary function here"""
        raise NotImplementedError()

    @classmethod
    def build(cls, node: Node) -> RunnableLambda:
        try:
            built_node = cls(**node.params)
        except ValidationError as ex:
            raise PipelineNodeBuildError(ex)

        return RunnableLambda(built_node._invoke, name=cls.__name__)


class PipelinePreBuiltNode(BaseModel, ABC):
    """Pipeline node that is either a single or a combination of Langchain Runnables

    Define required parameters as typed fields. Compose the runnable in `get_runnable`

    Example:
        class FunNode(PipelinePreBuildNode):
            required_parameter_1: CustomType
            optional_parameter_1: Optional[CustomType] = None

            def get_runnable(self, input) -> str:
                if self.optional_parameter_1:
                    return PromptTemplate.from_template(template=self.required_parameter_1)
                else:
                    return LLMResponse.build(node)
    """

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def build(cls, node: Node) -> Runnable:
        try:
            built_node = cls(**node.params)
        except ValidationError as ex:
            raise PipelineNodeBuildError(ex)

        return built_node.get_runnable(node)

    def get_runnable(self, node: Node) -> Runnable:
        """Get a predefined runnable to be used in the pipeline"""
        raise NotImplementedError
