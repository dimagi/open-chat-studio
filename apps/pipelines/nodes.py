from jinja2.sandbox import SandboxedEnvironment
from langchain_core.runnables import (
    ConfigurableField,
    Runnable,
    RunnableConfig,
    RunnablePassthrough,
    RunnableSerializable,
)


class PipelineNode(RunnableSerializable):
    typed_variables: int | None = None

    def get_config_param(self, config: RunnableConfig, name: str):
        return config.get("configurable", {})[name]

    def build(self) -> Runnable:
        # returns the runnable
        return RunnablePassthrough

    # def invoke(self, input, config, **kwargs):
    #     # set any required variables
    #     return super().invoke(input, config, **kwargs)


class PipelineJinjaTemplate(str):
    pass


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
