import json
from functools import partial

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain_core.messages import BaseMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda, RunnablePassthrough
from langchain_core.runnables.utils import Input
from pydantic import Field, create_model

from apps.experiments.models import ExperimentSession, ParticipantData
from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.graph import Node
from apps.pipelines.nodes.base import PipelineNode, PipelineState
from apps.pipelines.nodes.types import LlmModel, LlmProviderId, LlmTemperature, PipelineJinjaTemplate
from apps.pipelines.tasks import send_email_from_pipeline
from apps.service_providers.exceptions import ServiceProviderConfigError


class RenderTemplate(PipelineNode):
    __human_name__ = "Render a template"
    template_string: PipelineJinjaTemplate

    def get_runnable(self, node: Node, state: PipelineState) -> Runnable:
        def fn(input: Input):
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

        return RunnableLambda(fn, name=self.__class__.__name__)


class LLMResponse(PipelineNode):
    __human_name__ = "LLM response"

    llm_provider_id: LlmProviderId
    llm_model: LlmModel
    llm_temperature: LlmTemperature = 1.0

    def get_runnable(self, node: Node, state: PipelineState) -> Runnable:
        from apps.service_providers.models import LlmProvider

        try:
            provider = LlmProvider.objects.get(id=self.llm_provider_id)
            service = provider.get_llm_service()
        except LlmProvider.DoesNotExist:
            raise PipelineNodeBuildError(f"LLM provider with id {self.llm_provider_id} does not exist")
        except ServiceProviderConfigError as e:
            raise PipelineNodeBuildError("There was an issue configuring the LLM service provider") from e

        return service.get_chat_model(self.llm_model, self.llm_temperature)


class CreateReport(LLMResponse):
    __human_name__ = "Create a report"

    prompt: str = (
        "Make a summary of the following text: {input}. "
        "Output it as JSON with a single key called 'summary' with the summary."
    )

    def get_runnable(self, node: Node, state: PipelineState) -> Runnable:
        return PromptTemplate.from_template(template=self.prompt) | super().get_runnable(node, state)


class SendEmail(PipelineNode):
    __human_name__ = "Send an email"
    recipient_list: str
    subject: str

    def get_runnable(self, node: Node, state: PipelineState) -> RunnableLambda:
        def fn(input: Input):
            send_email_from_pipeline.delay(
                recipient_list=self.recipient_list.split(","), subject=self.subject, message=input
            )
            return input

        return RunnableLambda(fn, name=self.__class__.__name__)


class Passthrough(PipelineNode):
    __human_name__ = "Do Nothing"

    def get_runnable(self, node: Node, state: PipelineState) -> RunnableLambda:
        def fn(input: Input, config: RunnableConfig):
            self.logger(config).debug(f"Returning input: '{input}' without modification")
            return input

        return RunnableLambda(fn, name=self.__class__.__name__)


class ExtractStructuredDataBasic(LLMResponse):
    __human_name__ = "Extract structured data (Basic)"
    data_schema: str

    def get_runnable(self, node: Node, state: PipelineState) -> RunnableLambda:
        json_schema = self.to_json_schema(json.loads(self.data_schema))
        session = ExperimentSession.objects.get(id=state.get("experiment_session_id"))
        participant_data = session.get_participant_data()
        prompt = PromptTemplate.from_template(template="{input}.\nCurrent user data: {participant_data}")
        return (
            {"input": RunnablePassthrough()}
            | RunnablePassthrough.assign(participant_data=RunnableLambda(lambda x: participant_data))
            | prompt
            | super().get_runnable(node, state).with_structured_output(json_schema)
        )

    def to_json_schema(self, data: dict):
        pydantic_schema = {}
        for k, v in data.items():
            pydantic_schema[k] = (str | None, Field(description=v))
        Model = create_model("DataModel", **pydantic_schema)
        schema = Model.model_json_schema()
        # The schema needs a description in order to comply with function calling APIs
        schema["description"] = ""
        return schema


class UpdateParticipantMemory(PipelineNode):
    """A simple component to merge the the input data into the participant's memory data. If key_name is specified
    the input data will be merged with `key_name` as the key name.
    """

    __human_name__ = "Update participant memory"
    key_name: str | None = None

    def get_runnable(self, node: Node, state: PipelineState) -> RunnableLambda:
        node = node

        def fn(input: Input, config: RunnableConfig, state: PipelineState):
            """Input should be a python dictionary"""
            session = ExperimentSession.objects.get(id=state.get("experiment_session_id"))
            extracted_data = {self.key_name: input} if self.key_name else input
            try:
                participant_data = ParticipantData.objects.for_experiment(session.experiment).get(
                    participant=session.participant
                )
                participant_data.data = participant_data.data | extracted_data
                participant_data.save()
            except ParticipantData.DoesNotExist:
                ParticipantData.objects.create(
                    participant=session.participant,
                    content_type__model="experiment",
                    object_id=session.experiment.id,
                    team=session.team,
                    data=extracted_data,
                )

        return RunnableLambda(partial(fn, state=state), name=self.__class__.__name__)
