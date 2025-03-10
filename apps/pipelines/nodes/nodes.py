import datetime
import inspect
import json
import random
import time
from typing import Literal

import tiktoken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import TextChoices
from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from langchain_core.messages import BaseMessage
from langchain_core.prompts import MessagesPlaceholder, PromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, Field, create_model, field_validator
from pydantic.config import ConfigDict
from pydantic_core import PydanticCustomError
from pydantic_core.core_schema import FieldValidationInfo
from RestrictedPython import compile_restricted, safe_builtins, safe_globals

from apps.assistants.models import OpenAiAssistant
from apps.chat.agent.tools import get_node_tools
from apps.chat.conversation import compress_chat_history, compress_pipeline_chat_history
from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentSession, ParticipantData
from apps.pipelines.exceptions import PipelineNodeBuildError, PipelineNodeRunError
from apps.pipelines.models import Node, PipelineChatHistory, PipelineChatHistoryModes, PipelineChatHistoryTypes
from apps.pipelines.nodes.base import (
    NodeSchema,
    OptionsSource,
    PipelineNode,
    PipelineState,
    UiSchema,
    Widgets,
    deprecated_node,
)
from apps.pipelines.nodes.helpers import ParticipantDataProxy
from apps.pipelines.tasks import send_email_from_pipeline
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.llm_service.adapters import AssistantAdapter, ChatAdapter
from apps.service_providers.llm_service.history_managers import PipelineHistoryManager
from apps.service_providers.llm_service.runnables import (
    AgentAssistantChat,
    AgentLLMChat,
    AssistantChat,
    ChainOutput,
    SimpleLLMChat,
)
from apps.service_providers.models import LlmProviderModel
from apps.utils.prompt import OcsPromptTemplate, PromptVars, validate_prompt_variables


class RenderTemplate(PipelineNode):
    """Renders a Jinja template"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Render a template", documentation_link=settings.DOCUMENTATION_LINKS["node_template"]
        )
    )

    template_string: str = Field(
        description="Use {{your_variable_name}} to refer to designate input",
        json_schema_extra=UiSchema(widget=Widgets.expandable_text),
    )

    def _process(self, input, node_id: str, **kwargs) -> PipelineState:
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
        output = template.render(content)
        return PipelineState.from_node_output(node_name=self.name, node_id=node_id, output=output)


class LLMResponseMixin(BaseModel):
    llm_provider_id: int = Field(..., title="LLM Model", json_schema_extra=UiSchema(widget=Widgets.llm_provider_model))
    llm_provider_model_id: int = Field(..., json_schema_extra=UiSchema(widget=Widgets.none))
    llm_temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, title="Temperature", json_schema_extra=UiSchema(widget=Widgets.range)
    )

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
    history_type: PipelineChatHistoryTypes = Field(
        PipelineChatHistoryTypes.NONE,
        json_schema_extra=UiSchema(widget=Widgets.history, enum_labels=PipelineChatHistoryTypes.labels),
    )
    history_name: str | None = Field(
        None,
        json_schema_extra=UiSchema(
            widget=Widgets.none,
        ),
    )
    history_mode: PipelineChatHistoryModes = Field(
        None,
        json_schema_extra=UiSchema(widget=Widgets.history_mode, enum_labels=PipelineChatHistoryModes.labels),
    )
    user_max_token_limit: int | None = Field(
        None,
        json_schema_extra=UiSchema(
            widget=Widgets.none,
        ),
    )
    max_history_length: int = Field(
        10,
        json_schema_extra=UiSchema(
            widget=Widgets.none,
        ),
    )

    @field_validator("history_name")
    def validate_history_name(cls, value, info: FieldValidationInfo):
        if info.data.get("history_type") == PipelineChatHistoryTypes.NAMED and not value:
            raise PydanticCustomError("invalid_history_name", "A history name is required for named history")
        return value

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
                max_token_limit=(
                    self.user_max_token_limit
                    if self.user_max_token_limit is not None
                    else self.get_llm_provider_model().max_token_limit
                ),
                input_messages=input_messages,
                history_mode=self.history_mode,
            )

        try:
            history: PipelineChatHistory = session.pipeline_chat_history.get(
                type=self.history_type, name=self._get_history_name(node_id)
            )
        except PipelineChatHistory.DoesNotExist:
            return []
        return compress_pipeline_chat_history(
            pipeline_chat_history=history,
            llm=self.get_chat_model(),
            max_token_limit=(
                self.user_max_token_limit
                if self.user_max_token_limit is not None
                else self.get_llm_provider_model().max_token_limit
            ),
            input_messages=input_messages,
            keep_history_len=self.max_history_length,
            history_mode=self.history_mode,
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


@deprecated_node(message="Use the 'LLM' node instead.")
class LLMResponse(PipelineNode, LLMResponseMixin):
    """Calls an LLM with the given input"""

    model_config = ConfigDict(json_schema_extra=NodeSchema(label="LLM response"))

    def _process(self, input, node_id: str, **kwargs) -> PipelineState:
        llm = self.get_chat_model()
        output = llm.invoke(input, config=self._config)
        return PipelineState.from_node_output(node_name=self.name, node_id=node_id, output=output.content)


class LLMResponseWithPrompt(LLMResponse, HistoryMixin):
    """Uses and LLM to respond to the input."""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(label="LLM", documentation_link=settings.DOCUMENTATION_LINKS["node_llm"])
    )

    source_material_id: int | None = Field(
        None, json_schema_extra=UiSchema(widget=Widgets.select, options_source=OptionsSource.source_material)
    )
    prompt: str = Field(
        default="You are a helpful assistant. Answer the user's query as best you can",
        json_schema_extra=UiSchema(widget=Widgets.expandable_text),
    )
    tools: list[str] = Field(
        default_factory=list,
        description="The tools to enable for the bot",
        json_schema_extra=UiSchema(widget=Widgets.multiselect, options_source=OptionsSource.agent_tools),
    )
    custom_actions: list[str] = Field(
        default_factory=list,
        description="Custom actions to enable for the bot",
        json_schema_extra=UiSchema(widget=Widgets.multiselect, options_source=OptionsSource.custom_actions),
    )

    @field_validator("tools", mode="before")
    def check_prompt_variables(cls, value: str, info: FieldValidationInfo):
        if not value:
            return []
        context = {"prompt": info.data["prompt"], "source_material": info.data["source_material_id"], "tools": value}
        try:
            validate_prompt_variables(form_data=context, prompt_key="prompt", known_vars=set(PromptVars.values))
        except ValidationError as e:
            raise PydanticCustomError("invalid_prompt", e.error_dict["prompt"][0].message)
        return value

    @field_validator("custom_actions", mode="before")
    def validate_custom_actions(cls, value):
        if value is None:
            return []
        return value

    def _process(self, input, state: PipelineState, node_id: str) -> PipelineState:
        session: ExperimentSession | None = state.get("experiment_session")
        pipeline_version = state.get("pipeline_version")
        # Get runnable
        provider_model = self.get_llm_provider_model()
        chat_model = self.get_chat_model()
        history_manager = PipelineHistoryManager.for_llm_chat(
            session=session,
            node_id=node_id,
            history_type=self.history_type,
            history_name=self.history_name,
            max_token_limit=provider_model.max_token_limit,
            chat_model=chat_model,
        )

        node = Node.objects.get(flow_id=node_id, pipeline__version_number=pipeline_version)
        tools = get_node_tools(node, session)
        chat_adapter = ChatAdapter.for_pipeline(
            session=session, node=self, llm_service=self.get_llm_service(), provider_model=provider_model, tools=tools
        )
        if self.tools_enabled():
            chat = AgentLLMChat(adapter=chat_adapter, history_manager=history_manager)
        else:
            chat = SimpleLLMChat(adapter=chat_adapter, history_manager=history_manager)

        # Invoke runnable
        result = chat.invoke(input=input)
        return PipelineState.from_node_output(node_name=self.name, node_id=node_id, output=result.output)

    def tools_enabled(self) -> bool:
        return len(self.tools) > 0 or len(self.custom_actions) > 0


class SendEmail(PipelineNode):
    """Send the input to the node to the list of addresses provided"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Send an email", documentation_link=settings.DOCUMENTATION_LINKS["node_email"]
        )
    )

    recipient_list: str = Field(description="A comma-separated list of email addresses")
    subject: str

    @field_validator("recipient_list", mode="before")
    def recipient_list_has_valid_emails(cls, value):
        value = value or ""
        for email in [email.strip() for email in value.split(",")]:
            try:
                validate_email(email)
            except ValidationError:
                raise PydanticCustomError("invalid_recipient_list", "Invalid list of emails addresses")
        return value

    def _process(self, input, node_id: str, **kwargs) -> PipelineState:
        send_email_from_pipeline.delay(
            recipient_list=self.recipient_list.split(","), subject=self.subject, message=input
        )
        return PipelineState.from_node_output(node_name=self.name, node_id=node_id, output=input)


class Passthrough(PipelineNode):
    """Returns the input without modification"""

    model_config = ConfigDict(json_schema_extra=NodeSchema(label="Do Nothing", can_add=False))

    def _process(self, input, state: PipelineState, node_id: str) -> PipelineState:
        if self.logger:
            self.logger.debug(f"Returning input: '{input}' without modification", input=input, output=input)
        return PipelineState.from_node_output(node_name=self.name, node_id=node_id, output=input)


class StartNode(Passthrough):
    """The start of the pipeline"""

    name: str = "start"
    model_config = ConfigDict(json_schema_extra=NodeSchema(label="Start", flow_node_type="startNode"))


class EndNode(Passthrough):
    """The end of the pipeline"""

    name: str = "end"
    model_config = ConfigDict(json_schema_extra=NodeSchema(label="End", flow_node_type="endNode"))


@deprecated_node(message="Use the 'Router' node instead.")
class BooleanNode(Passthrough):
    """Branches based whether the input matches a certain value"""

    model_config = ConfigDict(json_schema_extra=NodeSchema(label="Conditional Node"))

    input_equals: str

    def _process_conditional(self, state: PipelineState, node_id: str | None = None) -> Literal["true", "false"]:
        if self.input_equals == state["messages"][-1]:
            return "true"
        return "false"

    def get_output_map(self):
        """A mapping from the output handles on the frontend to the return values of process_conditional"""
        return {"output_0": "true", "output_1": "false"}


class RouterMixin(BaseModel):
    keywords: list[str] = Field(default_factory=list, json_schema_extra=UiSchema(widget=Widgets.keywords))

    @field_validator("keywords")
    def ensure_keywords_exist(cls, value, info: FieldValidationInfo):
        if not all(entry for entry in value):
            raise PydanticCustomError("invalid_keywords", "Keywords cannot be empty")

        if len(set(value)) != len(value):
            raise PydanticCustomError("invalid_keywords", "Keywords must be unique")

        return value

    def _get_keyword(self, result: str):
        keyword = result.lower().strip()
        if keyword in [k.lower() for k in self.keywords]:
            return keyword.lower()
        else:
            return self.keywords[0].lower()

    def get_output_map(self):
        """Returns a mapping of the form:
        {"output_1": "keyword 1", "output_2": "keyword_2", ...} where keywords are defined by the user
        """
        return {f"output_{output_num}": keyword.lower() for output_num, keyword in enumerate(self.keywords)}


class RouterNode(RouterMixin, Passthrough, HistoryMixin):
    """Routes the input to one of the linked nodes using an LLM"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="LLM Router",
            documentation_link=settings.DOCUMENTATION_LINKS["node_llm_router"],
            field_order=["llm_provider_id", "llm_temperature", "history_type", "prompt", "keywords"],
        )
    )

    prompt: str = Field(
        default="You are an extremely helpful router",
        min_length=1,
        json_schema_extra=UiSchema(widget=Widgets.expandable_text),
    )

    def _process_conditional(self, state: PipelineState, node_id=None):
        prompt = OcsPromptTemplate.from_messages(
            [("system", self.prompt), MessagesPlaceholder("history", optional=True), ("human", "{input}")]
        )

        node_input = state["messages"][-1]
        context = {"input": node_input}
        session: ExperimentSession | None = state.get("experiment_session")

        if self.history_type != PipelineChatHistoryTypes.NONE and session:
            input_messages = prompt.format_messages(**context)
            context["history"] = self._get_history(session, node_id, input_messages)

        chain = prompt | self.get_chat_model()

        result = chain.invoke(context, config=self._config)
        keyword = self._get_keyword(result.content)
        if session:
            self._save_history(session, node_id, node_input, keyword)
        return keyword


class StaticRouterNode(RouterMixin, Passthrough):
    """Routes the input to a linked node using the temp state of the pipeline or participant data"""

    class DataSource(TextChoices):
        participant_data = "participant_data", "Participant Data"
        temp_state = "temp_state", "Temporary State"

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Static Router",
            documentation_link=settings.DOCUMENTATION_LINKS["node_static_router"],
            field_order=["data_source", "route_key", "keywords"],
        )
    )

    data_source: DataSource = Field(
        DataSource.participant_data,
        description="The source of the data to use for routing",
        json_schema_extra=UiSchema(enum_labels=DataSource.labels),
    )
    route_key: str = Field(..., description="The key in the data to use for routing")

    def _process_conditional(self, state: PipelineState, node_id=None):
        from apps.service_providers.llm_service.prompt_context import SafeAccessWrapper

        if self.data_source == self.DataSource.participant_data:
            data = ParticipantDataProxy.from_state(state).get()
        else:
            data = state["temp_state"]

        formatted_key = f"{{data.{self.route_key}}}"
        try:
            result = formatted_key.format(data=SafeAccessWrapper(data))
        except KeyError:
            result = ""

        return self._get_keyword(result)


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

    def _process(self, input, state: PipelineState, node_id: str, **kwargs) -> PipelineState:
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
        output = input if self.is_passthrough else json.dumps(new_reference_data)
        return PipelineState.from_node_output(node_name=self.name, node_id=node_id, output=output)

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
    """Extract structured data from the input"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Extract Structured Data",
            documentation_link=settings.DOCUMENTATION_LINKS["node_extract_structured_data"],
        )
    )

    data_schema: str = Field(
        default='{"name": "the name of the user"}',
        description="A JSON object structure where the key is the name of the field and the value the description",
        json_schema_extra=UiSchema(widget=Widgets.expandable_text),
    )

    @property
    def is_passthrough(self) -> bool:
        return False


class ExtractParticipantData(ExtractStructuredDataNodeMixin, LLMResponse, StructuredDataSchemaValidatorMixin):
    """Extract structured data and saves it as participant data"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="Update Participant Data",
            documentation_link=settings.DOCUMENTATION_LINKS["node_update_participant_data"],
        )
    )

    data_schema: str = Field(
        default='{"name": "the name of the user"}',
        description="A JSON object structure where the key is the name of the field and the value the description",
        json_schema_extra=UiSchema(widget=Widgets.expandable_text),
    )
    key_name: str = ""

    @property
    def is_passthrough(self) -> bool:
        return True

    def get_reference_data(self, state) -> dict:
        """Returns the participant data as reference. If there is a `key_name`, the value in the participant data
        corresponding to that key will be returned insteadg
        """
        session = state.get("experiment_session")
        if not session:
            return {}

        participant_data = (
            ParticipantData.objects.for_experiment(session.experiment).filter(participant=session.participant).first()
        )
        if not participant_data:
            return {}

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
        session = state.get("experiment_session")
        if not session:
            return

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
                experiment=session.experiment,
                team=session.team,
                data=output,
            )


class AssistantNode(PipelineNode):
    """Calls an OpenAI assistant"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(
            label="OpenAI Assistant", documentation_link=settings.DOCUMENTATION_LINKS["node_assistant"]
        )
    )

    assistant_id: int = Field(
        ..., json_schema_extra=UiSchema(widget=Widgets.select, options_source=OptionsSource.assistant)
    )
    citations_enabled: bool = Field(
        default=True,
        description="Whether to include cited sources in responses",
        json_schema_extra=UiSchema(widget=Widgets.toggle),
    )
    input_formatter: str = Field("", description="(Optional) Use {input} to designate the user input")

    @field_validator("input_formatter")
    def ensure_input_variable_exists(cls, value):
        value = value or ""
        acceptable_var = "input"
        if value:
            prompt_variables = set(PromptTemplate.from_template(value).input_variables)
            if acceptable_var not in prompt_variables:
                raise PydanticCustomError("invalid_input_formatter", "The input formatter must contain {input}")

            acceptable_vars = set([acceptable_var])
            extra_vars = prompt_variables - acceptable_vars
            if extra_vars:
                raise PydanticCustomError("invalid_input_formatter", "Only {input} is allowed")

    def _process(self, input, state: PipelineState, node_id: str, **kwargs) -> PipelineState:
        try:
            assistant = OpenAiAssistant.objects.get(id=self.assistant_id)
        except OpenAiAssistant.DoesNotExist:
            raise PipelineNodeBuildError(f"Assistant {self.assistant_id} does not exist")

        session: ExperimentSession | None = state.get("experiment_session")
        runnable = self._get_assistant_runnable(assistant, session=session, node_id=node_id)
        attachments = self._get_attachments(state)
        chain_output: ChainOutput = runnable.invoke(input, config=self._config, attachments=attachments)
        output = chain_output.output

        return PipelineState.from_node_output(
            node_name=self.name,
            node_id=node_id,
            output=output,
            message_metadata={
                "input": runnable.adapter.get_message_metadata(ChatMessageType.HUMAN),
                "output": runnable.adapter.get_message_metadata(ChatMessageType.AI),
            },
        )

    def _get_attachments(self, state) -> list:
        return [att for att in state.get("temp_state", {}).get("attachments", []) if att.upload_to_assistant]

    def _get_assistant_runnable(self, assistant: OpenAiAssistant, session: ExperimentSession, node_id: str):
        trace_service = session.experiment.trace_service
        if trace_service:
            trace_service.initialize_from_callback_manager(self._config.get("callbacks"))

        history_manager = PipelineHistoryManager.for_assistant()
        adapter = AssistantAdapter.for_pipeline(session=session, node=self, trace_service=trace_service)
        if assistant.tools_enabled:
            return AgentAssistantChat(adapter=adapter, history_manager=history_manager)
        else:
            return AssistantChat(adapter=adapter, history_manager=history_manager)


CODE_NODE_DOCS = f"{settings.DOCUMENTATION_BASE_URL}{settings.DOCUMENTATION_LINKS['node_code']}"
DEFAULT_FUNCTION = f"""# You must define a main function, which takes the node input as a string.
# Return a string to pass to the next node.

# Learn more about Python nodes at {CODE_NODE_DOCS}

def main(input: str, **kwargs) -> str:
    return input
"""


class CodeNode(PipelineNode):
    """Runs python"""

    model_config = ConfigDict(
        json_schema_extra=NodeSchema(label="Python Node", documentation_link=settings.DOCUMENTATION_LINKS["node_code"])
    )
    code: str = Field(
        default=DEFAULT_FUNCTION,
        description="The code to run",
        json_schema_extra=UiSchema(widget=Widgets.code),
    )

    @field_validator("code")
    def validate_code(cls, value, info: FieldValidationInfo):
        if not value:
            value = DEFAULT_FUNCTION
        try:
            byte_code = compile_restricted(
                value,
                filename="<inline code>",
                mode="exec",
            )
            custom_locals = {}
            try:
                exec(byte_code, {}, custom_locals)
            except Exception as exc:
                raise PydanticCustomError("invalid_code", "{error}", {"error": str(exc)})

            try:
                main = custom_locals["main"]
            except KeyError:
                raise SyntaxError("You must define a 'main' function")

            for name, item in custom_locals.items():
                if name != "main" and inspect.isfunction(item):
                    raise SyntaxError(
                        "You can only define a single function, 'main' at the top level. "
                        "You may use nested functions inside that function if required"
                    )

            if list(inspect.signature(main).parameters) != ["input", "kwargs"]:
                raise SyntaxError("The main function should have the signature main(input, **kwargs) only.")

        except SyntaxError as exc:
            raise PydanticCustomError("invalid_code", "{error}", {"error": exc.msg})
        return value

    def _process(self, input: str, state: PipelineState, node_id: str) -> PipelineState:
        function_name = "main"
        byte_code = compile_restricted(
            self.code,
            filename="<inline code>",
            mode="exec",
        )

        custom_locals = {}
        custom_globals = self._get_custom_globals(state)
        kwargs = {"logger": self.logger}
        try:
            exec(byte_code, custom_globals, custom_locals)
            result = str(custom_locals[function_name](input, **kwargs))
        except Exception as exc:
            raise PipelineNodeRunError(exc) from exc
        return PipelineState.from_node_output(node_name=self.name, node_id=node_id, output=result)

    def _get_custom_globals(self, state: PipelineState):
        from RestrictedPython.Eval import (
            default_guarded_getitem,
            default_guarded_getiter,
        )

        custom_globals = safe_globals.copy()

        participant_data_proxy = ParticipantDataProxy.from_state(state)
        custom_globals.update(
            {
                "__builtins__": self._get_custom_builtins(),
                "json": json,
                "datetime": datetime,
                "time": time,
                "_getitem_": default_guarded_getitem,
                "_getiter_": default_guarded_getiter,
                "_write_": lambda x: x,
                "get_participant_data": participant_data_proxy.get,
                "set_participant_data": participant_data_proxy.set,
                "get_temp_state_key": self._get_temp_state_key(state),
                "set_temp_state_key": self._set_temp_state_key(state),
            }
        )
        return custom_globals

    def _get_temp_state_key(self, state: PipelineState):
        def get_temp_state_key(key_name: str):
            return state["temp_state"].get(key_name)

        return get_temp_state_key

    def _set_temp_state_key(self, state: PipelineState):
        def set_temp_state_key(key_name: str, value):
            if key_name in {"user_input", "outputs", "attachments"}:
                raise PipelineNodeRunError(f"Cannot set the '{key_name}' key of the temporary state")
            state["temp_state"][key_name] = value

        return set_temp_state_key

    def _get_custom_builtins(self):
        allowed_modules = {
            "json",
            "re",
            "datetime",
            "time",
            "random",
        }
        custom_builtins = safe_builtins.copy()
        custom_builtins.update(
            {
                "min": min,
                "max": max,
                "sum": sum,
                "abs": abs,
                "all": all,
                "any": any,
                "datetime": datetime,
                "random": random,
            }
        )

        def guarded_import(name, *args, **kwargs):
            if name not in allowed_modules:
                raise ImportError(f"Importing '{name}' is not allowed")
            return __import__(name, *args, **kwargs)

        custom_builtins["__import__"] = guarded_import
        return custom_builtins
