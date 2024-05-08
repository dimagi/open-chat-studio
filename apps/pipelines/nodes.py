import json

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain_core.messages import BaseMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import (
    ConfigurableField,
    Runnable,
    RunnableConfig,
    RunnableSerializable,
)

from apps.experiments.models import ExperimentSession
from apps.pipelines.graph import Node
from apps.pipelines.tasks import send_email_from_pipeline


class ExperimentSessionId(str):
    _description = "The Session ID"


class PipelineJinjaTemplate(str):
    _description = "The template to render"


class PipelineNode(RunnableSerializable):
    node: Node

    class Config:
        arbitrary_types_allowed = True

    def get_config_param(self, config: RunnableConfig, name: str):
        return config.get("configurable", {})[f"{name}_{self.node.id}"]

    @classmethod
    def build(cls, node: Node, session_id: ExperimentSessionId | None = None) -> Runnable:
        # we need the node when fetching the configurable parameters
        class_kwargs = {"node": node}

        # Construct the object with the configurable fields passed in
        # `node.data.params` if they are intended to be configurable
        configurable_param_names = {name for name in cls.__fields__.keys() if name not in ["name", "node"]}
        class_kwargs.update(
            {name: provided_param for name, provided_param in node.params.items() if name in configurable_param_names}
        )

        # Make all the configurable fields actually configurable
        configurable_fields = {
            name: ConfigurableField(
                # We use the node id to allow us to have more than one of the same runnable with different parameters
                id=f"{name}_{node.id}",
                name=name,
                description=getattr(field.type_, "_description", name),
            )
            for name, field in cls.__fields__.items()
            if name not in ["name", "node"]
        }
        if configurable_fields:
            return cls(**class_kwargs).configurable_fields(**configurable_fields)

        return cls(**class_kwargs)


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
    session_id: ExperimentSessionId

    @classmethod
    def build(cls, node, session_id: ExperimentSessionId):
        return PromptTemplate.from_template(
            template=node.params.get(
                "prompt",
                (
                    "Make a summary of the following text: {input}."
                    "Output it as JSON with a single key called 'summary' with the summary."
                ),
            )
        ).configurable_fields(
            template=ConfigurableField(id="prompt", name="prompt", description="The prompt to create the report")
        ) | LLMResponse.build(node, session_id)


class LLMResponse(PipelineNode):
    session_id: ExperimentSessionId

    @classmethod
    def build(cls, node: Node, session_id: ExperimentSessionId) -> Runnable:
        session = ExperimentSession.objects.get(id=session_id)
        return session.experiment.get_chat_model()


class SendEmail(PipelineNode):
    recipient_list: list[str]
    subject: str

    def invoke(self, input, config):
        send_email_from_pipeline.delay(self.recipient_list, subject=self.subject, message=input)
        return super().invoke(input, config)
