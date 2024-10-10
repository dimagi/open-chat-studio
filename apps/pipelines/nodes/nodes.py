import json
from typing import Literal

import tiktoken
from django.utils import timezone
from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import BaseMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field, create_model

from apps.channels.models import ChannelPlatform
from apps.chat.conversation import compress_chat_history
from apps.experiments.models import ExperimentSession, ParticipantData, SourceMaterial
from apps.pipelines.exceptions import PipelineNodeBuildError
from apps.pipelines.models import PipelineChatHistory, PipelineChatHistoryTypes
from apps.pipelines.nodes.base import PipelineNode, PipelineState
from apps.pipelines.nodes.types import (
    HistoryName,
    HistoryType,
    Keywords,
    LlmModel,
    LlmProviderId,
    LlmTemperature,
    MaxTokenLimit,
    NumOutputs,
    PipelineJinjaTemplate,
    Prompt,
    SourceMaterialId,
)
from apps.pipelines.tasks import send_email_from_pipeline
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.utils.time import pretty_date


class RenderTemplate(PipelineNode):
    __human_name__ = "Render a template"
    template_string: PipelineJinjaTemplate

    def _process(self, input, **kwargs) -> PipelineState:
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
    llm_model: LlmModel
    llm_temperature: LlmTemperature = 1.0
    history_type: HistoryType = PipelineChatHistoryTypes.NONE
    history_name: HistoryName | None = None
    max_token_limit: MaxTokenLimit = 8192

    def get_llm_service(self):
        from apps.service_providers.models import LlmProvider

        try:
            provider = LlmProvider.objects.get(id=self.llm_provider_id)
            return provider.get_llm_service()
        except LlmProvider.DoesNotExist:
            raise PipelineNodeBuildError(f"LLM provider with id {self.llm_provider_id} does not exist")
        except ServiceProviderConfigError as e:
            raise PipelineNodeBuildError("There was an issue configuring the LLM service provider") from e

    def get_chat_model(self):
        return self.get_llm_service().get_chat_model(self.llm_model, self.llm_temperature)


class LLMResponse(PipelineNode, LLMResponseMixin):
    __human_name__ = "LLM response"

    def _process(self, input, **kwargs) -> PipelineState:
        llm = self.get_chat_model()
        output = llm.invoke(input, config=self._config)
        return output.content


class LLMResponseWithPrompt(LLMResponse):
    __human_name__ = "LLM response with prompt"

    source_material_id: SourceMaterialId | None = None
    prompt: Prompt = "You are a helpful assistant. Answer the user's query as best you can: {input}"

    def _process(self, input, state: PipelineState, node_id: str) -> PipelineState:
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

    def _get_history(self, session: ExperimentSession, node_id: str, input_messages: list) -> list:
        if self.history_type == PipelineChatHistoryTypes.NONE:
            return []

        if self.history_type == PipelineChatHistoryTypes.GLOBAL:
            return compress_chat_history(
                chat=session.chat,
                llm=self.get_chat_model(),
                max_token_limit=self.max_token_limit,
                input_messages=input_messages,
            )

        if self.history_type == PipelineChatHistoryTypes.NAMED:
            history_name = self.history_name
        else:
            history_name = node_id

        try:
            history: PipelineChatHistory = session.pipeline_chat_history.get(type=self.history_type, name=history_name)
        except PipelineChatHistory.DoesNotExist:
            return []
        message_pairs = history.messages.all()
        return [message for message_pair in message_pairs for message in message_pair.as_tuples()]

    def _save_history(self, session: ExperimentSession, node_id: str, human_message: str, ai_message: str):
        if self.history_type == PipelineChatHistoryTypes.NONE:
            return

        if self.history_type == PipelineChatHistoryTypes.GLOBAL:
            # Global History is saved outside of the node
            return

        if self.history_type == PipelineChatHistoryTypes.NAMED:
            history_name = self.history_name
        else:
            history_name = node_id

        history, _ = session.pipeline_chat_history.get_or_create(type=self.history_type, name=history_name)
        message = history.messages.create(human_message=human_message, ai_message=ai_message)
        return message

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
    recipient_list: str
    subject: str

    def _process(self, input, **kwargs) -> PipelineState:
        send_email_from_pipeline.delay(
            recipient_list=self.recipient_list.split(","), subject=self.subject, message=input
        )


class Passthrough(PipelineNode):
    __human_name__ = "Do Nothing"

    def _process(self, input, state: PipelineState, node_id: str) -> PipelineState:
        self.logger.debug(f"Returning input: '{input}' without modification", input=input, output=input)
        return input


class BooleanNode(Passthrough):
    __human_name__ = "Boolean Node"
    input_equals: str

    def process_conditional(self, state: PipelineState) -> Literal["true", "false"]:
        if self.input_equals == state["messages"][-1]:
            return "true"
        return "false"

    def get_output_map(self):
        """A mapping from the output handles on the frontend to the return values of process_conditional"""
        return {"output_true": "true", "output_false": "false"}


class RouterNode(Passthrough, LLMResponseMixin):
    __human_name__ = "Router"
    llm_provider_id: LlmProviderId
    llm_model: LlmModel
    prompt: Prompt = "You are an extremely helpful router {input}"
    num_outputs: NumOutputs = 2
    keywords: Keywords = []

    def process_conditional(self, state: PipelineState):
        prompt = PromptTemplate.from_template(template=self.prompt)
        chain = prompt | self.get_chat_model()
        result = chain.invoke(state["messages"][-1], config=self._config)
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

    def _process(self, input, state: PipelineState, **kwargs) -> PipelineState:
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
        model_token_limit = 8192  # Get this from model metadata
        overlap_percentage = 0.2
        chunk_size_tokens = model_token_limit - prompt_token_count
        overlap_tokens = int(chunk_size_tokens * overlap_percentage)
        self.logger.debug(f"Chunksize in tokens: {chunk_size_tokens} with {overlap_tokens} tokens overlap")

        try:
            encoding = tiktoken.encoding_for_model(self.llm_model)
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


class ExtractStructuredData(ExtractStructuredDataNodeMixin, LLMResponse):
    __human_name__ = "Extract Structured Data"
    data_schema: str


class ExtractParticipantData(ExtractStructuredDataNodeMixin, LLMResponse):
    __human_name__ = "Extract Participant Data"
    data_schema: str
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
