import json
from functools import partial

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain_core.messages import BaseMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda, RunnablePassthrough
from langchain_core.runnables.utils import Input
from pydantic import Field, create_model

from apps.experiments.models import ParticipantData
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
        service = self.get_llm_service()
        return service.get_chat_model(self.llm_model, self.llm_temperature)

    def get_llm_service(self):
        from apps.service_providers.models import LlmProvider

        provider = LlmProvider.objects.get(id=self.llm_provider_id)
        try:
            return provider.get_llm_service()
        except LlmProvider.DoesNotExist:
            raise PipelineNodeBuildError(f"LLM provider with id {self.llm_provider_id} does not exist")
        except ServiceProviderConfigError as e:
            raise PipelineNodeBuildError("There was an issue configuring the LLM service provider") from e


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
        json_schema = ExtractStructuredDataBasic.to_json_schema(json.loads(self.data_schema))
        prompt = PromptTemplate.from_template(template="{input}.\nCurrent user data: {participant_data}")
        return (
            {"input": RunnablePassthrough()}
            | RunnablePassthrough.assign(participant_data=RunnableLambda(lambda x: self.get_participant_data(state)))
            | prompt
            | super().get_runnable(node, state).with_structured_output(json_schema)
        )

    def get_participant_data(self, state: PipelineState):
        session = self.experiment_session(state)
        return session.get_participant_data()

    @staticmethod
    def to_json_schema(data: dict):
        """Converts a dictionary to a JSON schema by first converting it to a Pydantic object and dumping it again.
        The input should be in the format {"key": "description", "key2": [{"key": "description"}]}

        Nested objects are not supported at the moment

        Input example 1:
        {"name": "the user's name", "surname": "the user's surname"}

        Input example 2:
        {"name": "the user's name", "pets": [{"name": "the pet's name": "type": "the type of animal"}]}

        """

        def _create_model_from_data(value_data, model_name: str):
            pydantic_schema = {}
            for key, value in value_data.items():
                if isinstance(value, str):
                    pydantic_schema[key] = (str | None, Field(description=value))
                elif isinstance(value, list):
                    model = _create_model_from_data(value[0], key.capitalize())
                    pydantic_schema[key] = (list[model], Field(description=f"A list of {key}"))
            return create_model(model_name, **pydantic_schema)

        Model = _create_model_from_data(data, "CustomModel")
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
            session = self.experiment_session(state)
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
