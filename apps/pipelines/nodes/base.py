from abc import ABC

from langchain_core.runnables import (
    Runnable,
)
from pydantic import BaseModel
from pydantic_core import ValidationError

from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.graph import Node


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
    def build(cls, node: Node) -> Runnable:
        try:
            built_node = cls(**node.params)
        except ValidationError as ex:
            raise PipelineNodeBuildError(ex)

        return built_node.get_runnable(node)

    def get_runnable(self, node: Node) -> Runnable:
        """Get a predefined runnable to be used in the pipeline"""
        raise NotImplementedError
