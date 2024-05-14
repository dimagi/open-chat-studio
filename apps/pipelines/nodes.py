import json
from abc import ABC
from typing import TypeAlias

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain_core.messages import BaseMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import (
    Runnable,
    RunnableLambda,
)
from langchain_core.runnables.utils import Input
from pydantic import BaseModel
from pydantic_core import ValidationError

from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.graph import Node
from apps.pipelines.tasks import send_email_from_pipeline

LlmProviderId: TypeAlias = int
LlmModel: TypeAlias = str
LlmTemperature: TypeAlias = float
PipelineJinjaTemplate: TypeAlias = str  # TODO: type PipelineJinjaTempate = str in python 3.12


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

        return RunnableLambda(built_node._invoke)


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


class RenderTemplate(PipelineLambdaNode):
    template_string: PipelineJinjaTemplate

    def _invoke(self, input: Input) -> str:
        env = SandboxedEnvironment()
        try:
            if isinstance(input, BaseMessage):
                content = json.loads(input.content)
            elif isinstance(input, dict):
                content = input
            else:
                content = json.loads(input)
        except json.JSONDecodeError:
            # As a last resort, just set the all the variables in the template to the input
            content = {var: input for var in meta.find_undeclared_variables(env.parse(self.template_string))}

        template = SandboxedEnvironment().from_string(self.template_string)
        return template.render(content)


class CreateReport(PipelinePreBuiltNode):
    prompt: str = "Make a summary of the following text: {input}. Output it as JSON with a single key called 'summary' with the summary."

    def get_runnable(self, node: Node) -> Runnable:
        return PromptTemplate.from_template(template=self.prompt) | LLMResponse.build(node)


class LLMResponse(PipelinePreBuiltNode):
    llm_provider_id: LlmProviderId
    llm_model: LlmModel
    llm_temperature: LlmTemperature = 1.0

    def get_runnable(self, node: Node) -> Runnable:
        from apps.service_providers.models import LlmProvider

        try:
            provider = LlmProvider.objects.get(id=self.llm_provider_id)
        except LlmProvider.DoesNotExist:
            raise PipelineNodeBuildError("LLM provider with id {provider_id} does not exist")

        service = provider.get_llm_service()
        return service.get_chat_model(self.llm_model, self.llm_temperature)


class SendEmail(PipelineLambdaNode):
    recipient_list: list[str]
    subject: str

    def _invoke(self, input: Input) -> str:
        send_email_from_pipeline.delay(recipient_list=self.recipient_list, subject=self.subject, message=input)
        # raise Exception("BLAHHH")
        return f"To: {', '.join(self.recipient_list)} \nSubject: {self.subject}\n\n{input}"
