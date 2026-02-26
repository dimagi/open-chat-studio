import json
import logging
import unicodedata
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

import tiktoken
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, BeforeValidator, Field, create_model, field_validator, model_validator
from pydantic_core import PydanticCustomError
from pydantic_core.core_schema import FieldValidationInfo

from apps.annotations.models import TagCategories
from apps.pipelines.exceptions import (
    PipelineNodeBuildError,
)
from apps.pipelines.models import (
    PipelineChatHistoryModes,
    PipelineChatHistoryTypes,
)
from apps.pipelines.nodes.base import (
    PipelineState,
    UiSchema,
    VisibleWhen,
    Widgets,
)
from apps.pipelines.nodes.history_middleware import (
    BaseNodeHistoryMiddleware,
    MaxHistoryLengthHistoryMiddleware,
    SummarizeHistoryMiddleware,
    TruncateTokensHistoryMiddleware,
)
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.llm_service import LlmService
from apps.service_providers.llm_service.default_models import LLM_MODEL_PARAMETERS
from apps.service_providers.llm_service.model_parameters import BasicParameters
from apps.service_providers.llm_service.retry import with_llm_retry
from apps.service_providers.models import LlmProviderModel
from apps.utils.langchain import dict_to_json_schema

if TYPE_CHECKING:
    from apps.experiments.models import ExperimentSession
    from apps.pipelines.nodes.context import NodeContext
    from apps.pipelines.repository import PipelineRepository

logger = logging.getLogger("ocs.pipelines.nodes")

OptionalInt = Annotated[int | None, BeforeValidator(lambda x: None if isinstance(x, str) and len(x) == 0 else x)]


@lru_cache
def get_llm_provider_model(llm_provider_model_id: int):
    try:
        return LlmProviderModel.objects.get(id=llm_provider_model_id)
    except LlmProviderModel.DoesNotExist:
        raise PipelineNodeBuildError(f"LLM provider model with id {llm_provider_model_id} does not exist") from None


@lru_cache
def get_llm_provider(llm_provider_id: int):
    from apps.service_providers.models import LlmProvider

    try:
        return LlmProvider.objects.get(id=llm_provider_id)
    except LlmProvider.DoesNotExist:
        return None


class OutputMessageTagMixin(BaseModel):
    tag: str = Field(
        default="",
        title="Message Tag",
        description="The tag that the output message should be tagged with",
    )

    @field_validator("tag", mode="after")
    @classmethod
    def normalize_tag(cls, value: str) -> str:
        return unicodedata.normalize("NFC", value)

    def get_output_tags(self) -> list[tuple[str, None]]:
        tags: list[tuple[str, None]] = []
        if self.tag:
            tags.append((self.tag, None))
        return tags


class LLMResponseMixin(BaseModel):
    llm_provider_id: int = Field(..., title="LLM Model", json_schema_extra=UiSchema(widget=Widgets.llm_provider_model))
    llm_provider_model_id: int = Field(..., json_schema_extra=UiSchema(widget=Widgets.none))
    llm_model_parameters: dict[str, Any] = Field(default_factory=dict, json_schema_extra=UiSchema(widget=Widgets.none))

    @model_validator(mode="before")
    @classmethod
    def ensure_default_parameters(cls, data) -> Self:
        if llm_provider_model_id := data.get("llm_provider_model_id"):
            model = get_llm_provider_model(llm_provider_model_id)
            params_cls = LLM_MODEL_PARAMETERS.get(model.name, BasicParameters)
            # Handle None explicitly by treating it as empty dict
            param_value = data.get("llm_model_parameters") or {}
            data["llm_model_parameters"] = params_cls.model_validate(param_value).model_dump()
        else:
            data["llm_model_parameters"] = {}
        return data

    @model_validator(mode="after")
    def validate_llm_model(self):
        # Ensure model is not deprecated
        try:
            model = get_llm_provider_model(self.llm_provider_model_id)
        except PipelineNodeBuildError as e:
            raise PydanticCustomError(
                "invalid_model",
                str(e),
                {"field": "llm_provider_id"},
            ) from None
        if model.deprecated:
            raise PydanticCustomError(
                "deprecated_model",
                f"LLM provider model '{model.name}' is deprecated.",
                {"field": "llm_provider_id"},
            )

        # Validate model parameters
        if params_cls := LLM_MODEL_PARAMETERS.get(model.name):
            params_cls.model_validate(self.llm_model_parameters)

        return self

    def get_llm_service(self, repo: "PipelineRepository") -> LlmService:
        try:
            provider = repo.get_llm_provider(self.llm_provider_id)
            if provider is None:
                raise PipelineNodeBuildError(f"LLM provider with id {self.llm_provider_id} does not exist") from None
            return provider.get_llm_service()
        except PipelineNodeBuildError:
            raise
        except ServiceProviderConfigError as e:
            raise PipelineNodeBuildError("There was an issue configuring the LLM service provider") from e

    def get_chat_model(self, repo: "PipelineRepository"):
        model_name = get_llm_provider_model(self.llm_provider_model_id).name
        logger.debug(f"Calling {model_name} with parameters: {self.llm_model_parameters}")
        return self.get_llm_service(repo=repo).get_chat_model(model_name, **self.llm_model_parameters)


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
        default=PipelineChatHistoryModes.SUMMARIZE,
        json_schema_extra=UiSchema(widget=Widgets.history_mode, enum_labels=PipelineChatHistoryModes.labels),
    )
    user_max_token_limit: int | None = Field(
        None,
        title="Token Limit",
        description="Maximum number of tokens before messages are summarized or truncated.",
        json_schema_extra=UiSchema(
            visible_when=VisibleWhen(
                field="history_mode",
                operator="in",
                value=["summarize", "truncate_tokens"],
            ),
        ),
    )
    max_history_length: int = Field(
        10,
        title="Max History Length",
        description="Chat history will only keep the most recent messages up to this limit.",
        json_schema_extra=UiSchema(
            visible_when=VisibleWhen(
                field="history_mode",
                value="max_history_length",
            ),
        ),
    )

    @field_validator("history_name")
    def validate_history_name(cls, value, info: FieldValidationInfo):
        if info.data.get("history_type") == PipelineChatHistoryTypes.NAMED and not value:
            raise PydanticCustomError("invalid_history_name", "A history name is required for named history")
        return value

    def _get_history_name(self):
        if self.history_type == PipelineChatHistoryTypes.NAMED:
            return self.history_name
        return self.node_id

    @property
    def history_is_disabled(self) -> bool:
        return self.history_type == PipelineChatHistoryTypes.NONE

    @property
    def use_session_history(self) -> bool:
        return self.history_type == PipelineChatHistoryTypes.GLOBAL

    def get_history_mode(self) -> PipelineChatHistoryModes:
        return self.history_mode or PipelineChatHistoryModes.SUMMARIZE

    def get_history(
        self,
        session: "ExperimentSession",
        repo: "PipelineRepository",
        exclude_message_id: int | None = None,
    ) -> list[BaseMessage]:
        """
        Returns the chat history messages for the node based on its history configuration.

        Global - Returns the chat history of the session up to the summary marker.
        Node/Named - Returns the chat history for this node from the pipeline chat history.
        None/Else - Returns an empty list.
        """
        if self.history_is_disabled:
            return []

        if self.use_session_history:
            return repo.get_session_messages_until_marker(
                chat=session.chat,
                marker=self.get_history_mode(),
                exclude_message_id=exclude_message_id,
            )

        history = repo.get_pipeline_chat_history(
            session=session,
            history_type=self.history_type,
            history_name=self._get_history_name(),
        )
        if history is None:
            return []
        return history.get_langchain_messages_until_marker(self.get_history_mode())

    def store_compression_checkpoint(
        self,
        compression_marker: str,
        checkpoint_message_id: int,
        repo: "PipelineRepository",
    ):
        """Persist the correct compression marker for this node's history mode.

        When `summary` is the literal `COMPRESSION_MARKER`, we record the node's current
        `history_mode` so future fetches know where to stop replaying messages. Otherwise, the
        provided `summary` captures the conversation state up to `checkpoint_message_id`.
        """
        history_mode = self.get_history_mode()
        if self.use_session_history:
            repo.save_compression_checkpoint_global(
                message_id=checkpoint_message_id,
                compression_marker=compression_marker,
                history_mode=history_mode,
            )
        else:
            repo.save_compression_checkpoint_pipeline(
                message_id=checkpoint_message_id,
                compression_marker=compression_marker,
                history_mode=history_mode,
            )

    def build_history_middleware(
        self,
        session: "ExperimentSession",
        system_message: BaseMessage,
        repo: "PipelineRepository",
    ) -> BaseNodeHistoryMiddleware | None:
        """Construct the history compression middleware configured for this node."""
        if self.history_is_disabled:
            return None

        history_mode = self.get_history_mode()

        compressor_kwargs = {
            "session": session,
            "node": self,
            "repo": repo,
        }
        if history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
            return MaxHistoryLengthHistoryMiddleware(max_history_length=self.max_history_length, **compressor_kwargs)

        specified_token_limit = (
            self.user_max_token_limit
            if self.user_max_token_limit is not None
            else get_llm_provider_model(self.llm_provider_model_id).max_token_limit
        )

        # Reserve space for the system message so trigger/keep thresholds reflect usable context
        system_message_tokens = count_tokens_approximately([system_message])
        token_limit = max(specified_token_limit - system_message_tokens, 100)

        if history_mode == PipelineChatHistoryModes.SUMMARIZE:
            return SummarizeHistoryMiddleware(token_limit=token_limit, **compressor_kwargs)

        if history_mode == PipelineChatHistoryModes.TRUNCATE_TOKENS:
            return TruncateTokensHistoryMiddleware(token_limit=token_limit, **compressor_kwargs)

    def save_history(
        self,
        session: "ExperimentSession",
        human_message: str,
        ai_message: str,
        repo: "PipelineRepository",
    ):
        if self.history_is_disabled:
            return

        if self.use_session_history:
            # Global History is saved outside of the node
            return

        history, _ = repo.get_or_create_pipeline_chat_history(
            session=session,
            history_type=self.history_type,
            history_name=self._get_history_name(),
        )
        message = repo.save_pipeline_chat_message(
            history=history,
            node_id=self.node_id,
            human_message=human_message,
            ai_message=ai_message,
        )
        return message


class RouterMixin(BaseModel):
    keywords: list[str] = Field(default_factory=list, json_schema_extra=UiSchema(widget=Widgets.keywords))
    default_keyword_index: int = Field(default=0, json_schema_extra=UiSchema(widget=Widgets.none))
    tag_output_message: bool = Field(
        default=False,
        description="Tag the output message with the selected route",
        json_schema_extra=UiSchema(widget=Widgets.toggle),
    )

    @field_validator("keywords")
    def ensure_keywords_are_uppercase(cls, value):
        if isinstance(value, list):
            return [entry.upper() for entry in value]
        return []

    @field_validator("keywords")
    def ensure_keywords_exist(cls, value, info: FieldValidationInfo):
        if not all(entry for entry in value):
            raise PydanticCustomError("invalid_keywords", "Keywords cannot be empty")
        if len(set(value)) != len(value):
            raise PydanticCustomError("invalid_keywords", "Keywords must be unique")
        return value

    def _create_router_schema(self):
        """Create a Pydantic model for structured router output"""
        return create_model(
            "RouterOutput", route=(Literal[tuple(self.keywords)], Field(description="Selected routing destination"))
        )

    def get_output_map(self):
        """Returns a mapping of the form:
        {"output_1": "keyword 1", "output_2": "keyword_2", ...} where keywords are defined by the user
        """
        return {f"output_{output_num}": keyword for output_num, keyword in enumerate(self.keywords)}

    def get_output_tags(self, selected_route, is_default_keyword: bool) -> list[tuple[str, str]]:
        if self.tag_output_message:
            tag_name = f"{self.name}:{selected_route}"
            tag_category = TagCategories.ERROR if is_default_keyword else TagCategories.BOT_RESPONSE
            if is_default_keyword:
                tag_name += ":default"
            return [(tag_name, tag_category)]
        return []


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

    def extraction_chain(self, tool_class, reference_data, repo: "PipelineRepository"):
        structured_output = super().get_chat_model(repo=repo).with_structured_output(tool_class)
        return self._prompt_chain(reference_data) | with_llm_retry(structured_output)

    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        repo = context.repo
        ToolClass = self.get_tool_class(json.loads(self.data_schema))
        reference_data = self.get_reference_data(context)
        prompt_token_count = self._get_prompt_token_count(reference_data, ToolClass.model_json_schema(), repo=repo)
        message_chunks = self.chunk_messages(context.input, prompt_token_count=prompt_token_count)

        new_reference_data = reference_data
        for message_chunk in message_chunks:
            chain = self.extraction_chain(tool_class=ToolClass, reference_data=new_reference_data, repo=repo)
            output = chain.invoke(message_chunk, config=self._config)
            output = output.model_dump()
            # TOOO: tracing
            # self.logger.info(
            #     f"Chunk {idx}",
            #     input=f"\nReference data:\n{new_reference_data}\nChunk data:\n{message_chunk}\n\n",
            #     output=f"\nExtracted data:\n{output}",
            # )
            new_reference_data = self.update_reference_data(output, reference_data)

        return self.get_node_output(context, new_reference_data)

    def get_node_output(self, context: "NodeContext", output_data) -> PipelineState:
        raise NotImplementedError()

    def get_reference_data(self, context: "NodeContext"):
        return ""

    def update_reference_data(self, new_data: dict, reference_data: dict) -> dict:
        return new_data

    def _get_prompt_token_count(self, reference_data: dict | str, json_schema: dict, repo: "PipelineRepository") -> int:
        llm = super().get_chat_model(repo=repo)
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
        llm_provider_model = get_llm_provider_model(self.llm_provider_model_id)
        model_token_limit = llm_provider_model.max_token_limit
        overlap_percentage = 0.2
        chunk_size_tokens = model_token_limit - prompt_token_count
        overlap_tokens = int(chunk_size_tokens * overlap_percentage)
        # TODO: tracing
        # self.logger.debug(f"Chunksize in tokens: {chunk_size_tokens} with {overlap_tokens} tokens overlap")

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

    def get_tool_class(self, data: dict):
        return dict_to_json_schema(data)


class StructuredDataSchemaValidatorMixin:
    @field_validator("data_schema")
    def validate_data_schema(cls, value):
        try:
            parsed_value = json.loads(value)
        except json.JSONDecodeError as e:
            raise PydanticCustomError("invalid_schema", "Invalid schema") from e

        if not isinstance(parsed_value, dict) or len(parsed_value) == 0:
            raise PydanticCustomError("invalid_schema", "Invalid schema")

        return value
