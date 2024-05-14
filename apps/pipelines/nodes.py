import json
from typing import TypeAlias

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain_core.messages import BaseMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import (
    Runnable,
    RunnableLambda,
)
from pydantic import BaseModel
from pydantic_core import ValidationError

from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.graph import Node
from apps.pipelines.tasks import send_email_from_pipeline


class LlmProviderId(str):
    _description = "The LLM Provider ID"


class LlmModel(str):
    _description = "The LLM Model Name"


class LlmTemperature(float):
    _description = "The LLM temperature"


PipelineJinjaTemplate: TypeAlias = str  # TODO: type PipelineJinjaTempate = str in python 3.12


class PipelineNode(BaseModel):
    class Config:
        arbitrary_types_allowed = True

    def _invoke(self, input):
        """ """

        pass

    @classmethod
    def build(cls, node: Node) -> Runnable:
        try:
            built_node = cls(**node.params)
        except ValidationError as ex:
            raise PipelineNodeBuildError(ex)

        return RunnableLambda(built_node._invoke)


class RenderTemplate(PipelineNode):
    template_string: PipelineJinjaTemplate

    def _invoke(self, input: BaseMessage | dict | str) -> str:
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


class CreateReport(PipelineNode):
    prompt: str | None = None

    @classmethod
    def build(cls, node):
        return PromptTemplate.from_template(
            template=node.params.get(
                "prompt",
                (
                    "Make a summary of the following text: {input}."
                    "Output it as JSON with a single key called 'summary' with the summary."
                ),
            )
        ) | LLMResponse.build(node)


class LLMResponse(PipelineNode):
    llm_provider_id: LlmProviderId
    llm_model: LlmModel
    llm_temperature: LlmTemperature = LlmTemperature(1.0)

    @classmethod
    def build(cls, node: Node) -> Runnable:
        from apps.service_providers.models import LlmProvider

        try:
            provider_id = node.params["llm_provider_id"]
        except KeyError:
            raise PipelineNodeBuildError("llm_provider_id is required")
        try:
            llm_model = node.params["llm_model"]
        except KeyError:
            raise PipelineNodeBuildError("llm_model is required")
        try:
            llm_temperature = node.params["llm_temperature"]
        except KeyError:
            llm_temperature = 1.0
        try:
            provider = LlmProvider.objects.get(id=provider_id)
        except LlmProvider.DoesNotExist:
            raise PipelineNodeBuildError("LLM provider with id {provider_id} does not exist")

        service = provider.get_llm_service()
        return service.get_chat_model(llm_model, llm_temperature)


class SendEmail(PipelineNode):
    recipient_list: list[str]
    subject: str

    def _invoke(self, input):
        send_email_from_pipeline.delay(recipient_list=self.recipient_list, subject=self.subject, message=input)
        # raise Exception("BLAHHH")
        return f"To: {', '.join(self.recipient_list)} \nSubject: {self.subject}\n\n{input}"
