import json

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain_core.messages import BaseMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import (
    Runnable,
)
from langchain_core.runnables.utils import Input

from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.graph import Node
from apps.pipelines.nodes.base import PipelineLambdaNode, PipelinePreBuiltNode
from apps.pipelines.nodes.types import LlmModel, LlmProviderId, LlmTemperature, PipelineJinjaTemplate
from apps.pipelines.tasks import send_email_from_pipeline


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
    prompt: str = (
        "Make a summary of the following text: {input}. "
        "Output it as JSON with a single key called 'summary' with the summary."
    )

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
