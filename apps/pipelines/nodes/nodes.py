import json
import re
from typing import Literal

import tiktoken
from django.utils import timezone
from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field, create_model, field_validator
from pydantic_core import PydanticCustomError

from apps.channels.models import ChannelPlatform
from apps.chat.conversation import compress_chat_history, compress_pipeline_chat_history
from apps.experiments.models import ExperimentSession, ParticipantData, SourceMaterial
from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.models import PipelineChatHistory, PipelineChatHistoryTypes
from apps.pipelines.nodes.base import PipelineNode, PipelineState
from apps.pipelines.nodes.types import (
    ExpandableText,
    HistoryName,
    HistoryType,
    Keywords,
    LlmProviderId,
    LlmProviderModelId,
    LlmTemperature,
    NumOutputs,
    SourceMaterialId,
)
from apps.pipelines.tasks import send_email_from_pipeline
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.models import LlmProviderModel
from apps.utils.time import pretty_date


class RenderTemplate(PipelineNode):
    __human_name__ = "Render a template"
    __node_description__ = "Renders a template"
    template_string: ExpandableText = Field(
        pattern=r"\{input\}",
        description="Use {input} to refer to designate input message",
    )

    def _process(self, input, **kwargs) -> str:
        def all_variables(in_):
            return {var: in_ for var in meta.find_undeclared_variables(env.parse(self.template_string))}

        env = SandboxedEnvironment()
        try:
            if isinstance(input, BaseMessage):
                content = json.loads(input.content)
            elif isinstance(input, dict):
                content = input
            else:
                content = json.loads(input)
                if not isinstance(content, dict):
                    # e.g. it was just a string or an int
                    content = all_variables(input)
        except json.JSONDecodeError:
            # As a last resort, just set the all the variables in the template to the input
            content = all_variables(input)
        template = SandboxedEnvironment().from_string(self.template_string)
        return template.render(content)


class LLMResponseMixin(BaseModel):
    llm_provider_id: LlmProviderId
    llm_provider_model_id: LlmProviderModelId
    llm_temperature: LlmTemperature = Field(default=1.0, gt=0.0, le=2.0)

    def get_llm_service(self):
        from apps.service_providers.models import LlmProvider

        try:
            provider = LlmProvider.objects.get(id=self.llm_provider_id)
            return provider.get_llm_service()
        except LlmProvider.DoesNotExist:
            raise PipelineNodeBuildError(f"LLM provider with id {self.llm_provider_id} does not exist")
        except ServiceProviderConfigError as e:
            raise PipelineNodeBuildError("There was an issue configuring the LLM service provider") from e

    def get_llm_provider_model(self):
        try:
            return LlmProviderModel.objects.get(id=self.llm_provider_model_id)
        except LlmProviderModel.DoesNotExist:
            raise PipelineNodeBuildError(f"LLM provider model with id {self.llm_provider_model_id} does not exist")

    def get_chat_model(self):
        return self.get_llm_service().get_chat_model(self.get_llm_provider_model().name, self.llm_temperature)


class HistoryMixin(LLMResponseMixin):
    history_type: HistoryType = PipelineChatHistoryTypes.NONE
    history_name: HistoryName | None = None

    def _get_history_name(self, node_id):
        if self.history_type == PipelineChatHistoryTypes.NAMED:
            return self.history_name
        return node_id

    def _get_history(self, session: ExperimentSession, node_id: str, input_messages: list) -> list[BaseMessage]:
        if self.history_type == PipelineChatHistoryTypes.NONE:
            return []

        if self.history_type == PipelineChatHistoryTypes.GLOBAL:
            return compress_chat_history(
                chat=session.chat,
                llm=self.get_chat_model(),
                max_token_limit=self.get_llm_provider_model().max_token_limit,
                input_messages=input_messages,
            )

        try:
            history: PipelineChatHistory = session.pipeline_chat_history.get(
                type=self.history_type, name=self._get_history_name(node_id)
            )
        except PipelineChatHistory.DoesNotExist:
            return []
        return compress_pipeline_chat_history(
            pipeline_chat_history=history,
            max_token_limit=self.get_llm_provider_model().max_token_limit,
            llm=self.get_chat_model(),
            input_messages=input_messages,
        )

    def _save_history(self, session: ExperimentSession, node_id: str, human_message: str, ai_message: str):
        if self.history_type == PipelineChatHistoryTypes.NONE:
            return

        if self.history_type == PipelineChatHistoryTypes.GLOBAL:
            # Global History is saved outside of the node
            return

        history, _ = session.pipeline_chat_history.get_or_create(
            type=self.history_type, name=self._get_history_name(node_id)
        )
        message = history.messages.create(human_message=human_message, ai_message=ai_message, node_id=node_id)
        return message


class LLMResponse(PipelineNode, LLMResponseMixin):
    __human_name__ = "LLM response"
    __node_description__ = "Calls an LLM with the given input"

    def _process(self, input, **kwargs) -> str:
        llm = self.get_chat_model()
        output = llm.invoke(input, config=self._config)
        return output.content


class LLMResponseWithPrompt(LLMResponse, HistoryMixin):
    __human_name__ = "LLM response with prompt"
    __node_description__ = "Calls an LLM with a prompt"

    source_material_id: SourceMaterialId | None = None
    prompt: ExpandableText = Field(
        default="You are a helpful assistant. Answer the user's query as best you can: {input}",
        pattern=r"\{input\}",
        description="Use {input} to refer to designate input message",
    )

    def _process(self, input, state: PipelineState, node_id: str) -> str:
        prompt = ChatPromptTemplate.from_messages(
            [("system", self.prompt), MessagesPlaceholder("history", optional=True), ("human", "{input}")]
        )
        context = self._get_context(input, state, prompt, node_id)
        if self.history_type != PipelineChatHistoryTypes.NONE:
            input_messages = prompt.invoke(context).to_messages()
            context["history"] = self._get_history(state["experiment_session"], node_id, input_messages)
        chain = prompt | super().get_chat_model()
        output = chain.invoke(context, config=self._config)
        self._save_history(state["experiment_session"], node_id, input, output.content)
        return output.content

    def _get_context(self, input, state: PipelineState, prompt: ChatPromptTemplate, node_id: str):
        session: ExperimentSession = state["experiment_session"]
        context = {"input": input}

        if "source_material" in prompt.input_variables and self.source_material_id is None:
            raise PipelineNodeBuildError("No source material set, but the prompt expects it")
        if "source_material" in prompt.input_variables and self.source_material_id:
            context["source_material"] = self._get_source_material().material

        if "participant_data" in prompt.input_variables:
            context["participant_data"] = self._get_participant_data(session)

        if "current_datetime" in prompt.input_variables:
            context["current_datetime"] = self._get_current_datetime(session)

        return context

    def _get_participant_data(self, session):
        if session.experiment_channel.platform == ChannelPlatform.WEB and session.participant.user is None:
            return ""
        return session.get_participant_data(use_participant_tz=True) or ""

    def _get_source_material(self):
        try:
            return SourceMaterial.objects.get(id=self.source_material_id)
        except SourceMaterial.DoesNotExist:
            raise PipelineNodeBuildError(f"Source material with id {self.source_material_id} does not exist")

    def _get_current_datetime(self, session):
        return pretty_date(timezone.now(), session.get_participant_timezone())


class SendEmail(PipelineNode):
    __human_name__ = "Send an email"
    __node_description__ = ""
    recipient_list: str = Field(description="A comma-separated list of email addresses")
    subject: str

    @field_validator("recipient_list", mode="before")
    def recipient_list_has_valid_emails(cls, value):
        pattern = r"^((\w+([-+.']\w+)*@\w+([-.]\w+)*\.\w+([-.]\w+)*)[, ]*)*$"
        if not value or not re.match(pattern, value):
            raise PydanticCustomError("invalid_recipient_list", "Invalid list of emails addresses")
        return value

    def _process(self, input, **kwargs) -> str:
        send_email_from_pipeline.delay(
            recipient_list=self.recipient_list.split(","), subject=self.subject, message=input
        )


class Passthrough(PipelineNode):
    __human_name__ = "Do Nothing"
    __node_description__ = ""

    def _process(self, input, state: PipelineState, node_id: str) -> str:
        self.logger.debug(f"Returning input: '{input}' without modification", input=input, output=input)
        return input


class BooleanNode(Passthrough):
    __human_name__ = "Boolean Node"
    __node_description__ = "Verifies whether the input is a certain value or not"
    input_equals: str

    def process_conditional(self, state: PipelineState, node_id: str | None = None) -> Literal["true", "false"]:
        if self.input_equals == state["messages"][-1]:
            return "true"
        return "false"

    def get_output_map(self):
        """A mapping from the output handles on the frontend to the return values of process_conditional"""
        return {"output_true": "true", "output_false": "false"}


class RouterNode(Passthrough, HistoryMixin):
    __human_name__ = "Router"
    __node_description__ = "Routes the input to one of the linked nodes"
    prompt: ExpandableText = Field(
        default="You are an extremely helpful router {input}",
        pattern=r"\{input\}",
        description="Use {input} to refer to designate input message",
    )
    num_outputs: NumOutputs = 2
    keywords: Keywords = []

    def process_conditional(self, state: PipelineState, node_id=None):
        prompt = ChatPromptTemplate.from_messages(
            [("system", self.prompt), MessagesPlaceholder("history", optional=True), ("human", "{input}")]
        )

        node_input = state["messages"][-1]
        context = {"input": node_input}

        if self.history_type != PipelineChatHistoryTypes.NONE:
            input_messages = prompt.invoke(context).to_messages()
            context["history"] = self._get_history(state["experiment_session"], node_id, input_messages)

        chain = prompt | self.get_chat_model()

        result = chain.invoke(context, config=self._config)
        keyword = self._get_keyword(result)
        self._save_history(state["experiment_session"], node_id, node_input, keyword)
        return keyword

    def _get_keyword(self, result):
        keyword = result.content.lower().strip()
        if keyword in [k.lower() for k in self.keywords]:
            return keyword.lower()
        else:
            return self.keywords[0].lower()

    def get_output_map(self):
        """Returns a mapping of the form:
        {"output_1": "keyword 1", "output_2": "keyword_2", ...} where keywords are defined by the user
        """
        return {f"output_{output_num}": keyword.lower() for output_num, keyword in enumerate(self.keywords)}


class ExtractStructuredDataNodeMixin:
    def _prompt_chain(self, reference_data):
        template = (
            "Extract user data using the current user data and conversation history as reference. Use JSON output."
            "\nCurrent user data:"
            "\n{reference_data}"
            "\nConversation history:"
            "\n{input}"
            "The conversation history should carry more weight in the outcome. It can change the user's current data"
        )
        prompt = PromptTemplate.from_template(template=template)
        return (
            {"input": RunnablePassthrough()}
            | RunnablePassthrough.assign(reference_data=RunnableLambda(lambda x: reference_data))
            | prompt
        )

    def extraction_chain(self, json_schema, reference_data):
        return self._prompt_chain(reference_data) | super().get_chat_model().with_structured_output(json_schema)

    def _process(self, input, state: PipelineState, **kwargs) -> str:
        json_schema = self.to_json_schema(json.loads(self.data_schema))
        reference_data = self.get_reference_data(state)
        prompt_token_count = self._get_prompt_token_count(reference_data, json_schema)
        message_chunks = self.chunk_messages(input, prompt_token_count=prompt_token_count)

        new_reference_data = reference_data
        for idx, message_chunk in enumerate(message_chunks, start=1):
            chain = self.extraction_chain(json_schema=json_schema, reference_data=new_reference_data)
            output = chain.invoke(message_chunk, config=self._config)
            self.logger.info(
                f"Chunk {idx}",
                input=f"\nReference data:\n{new_reference_data}\nChunk data:\n{message_chunk}\n\n",
                output=f"\nExtracted data:\n{output}",
            )
            new_reference_data = self.update_reference_data(output, reference_data)

        self.post_extraction_hook(new_reference_data, state)
        return json.dumps(new_reference_data)

    def post_extraction_hook(self, output, state):
        pass

    def get_reference_data(self, state):
        return ""

    def update_reference_data(self, new_data: dict, reference_data: dict) -> dict:
        return new_data

    def _get_prompt_token_count(self, reference_data: dict | str, json_schema: dict) -> int:
        llm = super().get_chat_model()
        prompt_chain = self._prompt_chain(reference_data)
        # If we invoke the chain with an empty input, we get the prompt without the conversation history, which
        # is what we want.
        output = prompt_chain.invoke(input="")
        json_schema_tokens = llm.get_num_tokens(json.dumps(json_schema))
        return llm.get_num_tokens(output.text) + json_schema_tokens

    def chunk_messages(self, input: str, prompt_token_count: int) -> list[str]:
        """Chunk messages using a splitter that considers the token count.
        Strategy:
        - chunk_size (in tokens) = The LLM's token limit - prompt_token_count
        - chunk_overlap is chosen to be 20%

        Note:
        Since we don't know the token limit of the LLM, we assume it to be 8192.
        """
        llm_provider_model = self.get_llm_provider_model()
        model_token_limit = llm_provider_model.max_token_limit
        overlap_percentage = 0.2
        chunk_size_tokens = model_token_limit - prompt_token_count
        overlap_tokens = int(chunk_size_tokens * overlap_percentage)
        self.logger.debug(f"Chunksize in tokens: {chunk_size_tokens} with {overlap_tokens} tokens overlap")

        try:
            encoding = tiktoken.encoding_for_model(llm_provider_model.name)
            encoding_name = encoding.name
        except KeyError:
            # The same encoder we use for llm.get_num_tokens_from_messages
            encoding_name = "gpt2"

        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name=encoding_name,
            chunk_size=chunk_size_tokens,
            chunk_overlap=overlap_tokens,
        )

        return text_splitter.split_text(input)

    def to_json_schema(self, data: dict):
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


class StructuredDataSchemaValidatorMixin:
    @field_validator("data_schema")
    def validate_data_schema(cls, value):
        try:
            parsed_value = json.loads(value)
        except json.JSONDecodeError:
            raise PydanticCustomError("invalid_schema", "Invalid schema")

        if not isinstance(parsed_value, dict) or len(parsed_value) == 0:
            raise PydanticCustomError("invalid_schema", "Invalid schema")

        return value


class ExtractStructuredData(ExtractStructuredDataNodeMixin, LLMResponse, StructuredDataSchemaValidatorMixin):
    __human_name__ = "Extract Structured Data"
    __node_description__ = "Extract structured data from the input"
    data_schema: ExpandableText = Field(
        default='{"name": "the name of the user"}',
        description="A key-value pair where the key is the name of the field and the value the description",
    )


class ExtractParticipantData(ExtractStructuredDataNodeMixin, LLMResponse, StructuredDataSchemaValidatorMixin):
    __human_name__ = "Extract Participant Data"
    __node_description__ = "Extract structured data and saves it as participant data"
    data_schema: ExpandableText = Field(
        default='{"name": "the name of the user"}',
        description="A key-value pair where the key is the name of the field and the value the description",
    )
    key_name: str | None = None

    def get_reference_data(self, state) -> dict:
        """Returns the participant data as reference. If there is a `key_name`, the value in the participant data
        corresponding to that key will be returned insteadg
        """
        session = state["experiment_session"]
        participant_data = (
            ParticipantData.objects.for_experiment(session.experiment).filter(participant=session.participant).first()
        )
        if not participant_data:
            return ""

        data = participant_data.data
        if self.key_name:
            # string, list or dict
            return data.get(self.key_name, "")
        return data

    def update_reference_data(self, new_data: dict, reference_data: dict | list | str) -> dict:
        if isinstance(reference_data, dict):
            # new_data may be a subset, superset or wholly different set of keys than the reference_data, so merge
            return reference_data | new_data

        # if reference data is a string or list, we cannot merge, so let's override
        return new_data

    def post_extraction_hook(self, output, state):
        session = state["experiment_session"]
        if self.key_name:
            output = {self.key_name: output}

        try:
            participant_data = ParticipantData.objects.for_experiment(session.experiment).get(
                participant=session.participant
            )
            participant_data.data = participant_data.data | output
            participant_data.save()
        except ParticipantData.DoesNotExist:
            ParticipantData.objects.create(
                participant=session.participant,
                content_object=session.experiment,
                team=session.team,
                data=output,
            )
