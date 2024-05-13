import json

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain_core.messages import BaseMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import (
    Runnable,
    RunnableConfig,
    RunnableSerializable,
)
from pydantic.v1 import ValidationError

from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.graph import Node
from apps.pipelines.tasks import send_email_from_pipeline


class LlmProviderId(str):
    _description = "The LLM Provider ID"


class LlmModel(str):
    _description = "The LLM Model Name"


class LlmTemperature(float):
    _description = "The LLM temperature"


class PipelineJinjaTemplate(str):
    _description = "The template to render"


class PipelineNode(RunnableSerializable):
    class Config:
        arbitrary_types_allowed = True

    def get_config_param(self, config: RunnableConfig, name: str):
        return config.get("configurable", {})[name]

    @classmethod
    def build(cls, node: Node) -> Runnable:
        # Construct the object with the configurable fields passed in
        # `node.data.params` if they are intended to be configurable
        configurable_param_names = {name for name in cls.__fields__.keys() if name not in ["name"]}
        class_kwargs = {
            name: provided_param for name, provided_param in node.params.items() if name in configurable_param_names
        }
        try:
            return cls(**class_kwargs)
        except ValidationError as ex:
            raise PipelineNodeBuildError(ex)


class RenderTemplate(PipelineNode):
    template_string: PipelineJinjaTemplate | None = None

    def invoke(self, input: BaseMessage | dict | str, config, **kwargs):
        env = SandboxedEnvironment()
        configured_template = (
            self.template_string if self.template_string else self.get_config_param(config, "template_string")
        )
        try:
            if isinstance(input, BaseMessage):
                content = json.loads(input.content)
            elif isinstance(input, dict):
                content = input
            else:
                content = json.loads(input)
        except json.JSONDecodeError:
            # As a last resort, just set the all the variables in the template to the input
            content = {var: input for var in meta.find_undeclared_variables(env.parse(configured_template))}

        template = SandboxedEnvironment().from_string(configured_template)
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

    def invoke(self, input, config):
        send_email_from_pipeline.delay(self.recipient_list, subject=self.subject, message=input)
        return super().invoke(input, config)
