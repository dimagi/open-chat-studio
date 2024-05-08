from jinja2.sandbox import SandboxedEnvironment
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import (
    ConfigurableField,
    Runnable,
    RunnableConfig,
    RunnablePassthrough,
    RunnableSerializable,
)

from apps.experiments.models import ExperimentSession
from apps.pipelines.graph import Node


class ExperimentSessionId(str):
    pass


class PipelineJinjaTemplate(str):
    pass


class PipelineNode(RunnableSerializable):
    def get_config_param(self, config: RunnableConfig, name: str):
        return config.get("configurable", {})[name]

    def build(self, node: dict, session_id: ExperimentSessionId | None = None) -> Runnable:
        # returns the runnable
        return RunnablePassthrough()


class RenderTemplate(PipelineNode):
    template_string: PipelineJinjaTemplate | None = None

    def invoke(self, input, config, **kwargs):
        configured_template = (
            self.template_string if self.template_string else self.get_config_param(config, "template_string")
        )
        template = SandboxedEnvironment().from_string(configured_template)
        return template.render(input)

    @classmethod
    def build(cls, node):
        return cls(template_string=node.params["template_string"]).configurable_fields(
            template_string=ConfigurableField(
                id="template_string", name="template_string", description="The Jinja Template"
            )
        )


class CreateReport(PipelineNode):
    prompt: str | None = None

    @classmethod
    def build(cls, node):
        return PromptTemplate.from_template(
            template="Make a summary of the following chat: {input}"
        ).configurable_fields(
            template=ConfigurableField(id="prompt", name="prompt", description="The prompt to create the report")
        )


class LLMResponse(PipelineNode):
    session_id: ExperimentSessionId

    @classmethod
    def build(cls, node: Node, session_id: ExperimentSessionId) -> Runnable:
        session = ExperimentSession.objects.get(id=session_id)
        return session.experiment.get_chat_model()
