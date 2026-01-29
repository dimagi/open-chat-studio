from __future__ import annotations

import logging
import re
from functools import cached_property
from io import BytesIO
from typing import TYPE_CHECKING, Literal

import pydantic
from django.db.models import Q
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from openai import NOT_GIVEN, OpenAI
from openai._base_client import SyncAPIClient
from pydantic import BaseModel

from apps.experiments.models import ExperimentSession
from apps.files.models import File
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.llm_service.callbacks import TokenCountingCallbackHandler
from apps.service_providers.llm_service.datamodels import LlmChatResponse
from apps.service_providers.llm_service.parsers import parse_output_for_anthropic
from apps.service_providers.llm_service.token_counters import (
    AnthropicTokenCounter,
    GeminiTokenCounter,
    GoogleVertexAITokenCounter,
    OpenAITokenCounter,
)
from apps.service_providers.llm_service.utils import (
    detangle_file_ids,
    extract_file_ids_from_ocs_citations,
    get_openai_container_file_contents,
)

logger = logging.getLogger("ocs.llm_service")

if TYPE_CHECKING:
    from apps.service_providers.llm_service.index_managers import IndexManager


class OpenAIBuiltinTool(dict):
    """A simple wrapper for OpenAI's builtin tools. This is used to easily distinquish OpenAI tools from dicts"""

    pass


class AnthropicBuiltinTool(dict):
    """A simple wrapper for Anthorpic's builtin tools. This is used to easily distinquish Anthorpic tools from dicts"""

    pass


class LlmService(pydantic.BaseModel):
    _type: str
    supports_transcription: bool = False
    supports_assistants: bool = False

    def get_raw_client(self) -> SyncAPIClient:
        raise NotImplementedError

    def get_assistant(self, assistant_id: str, as_agent=False):
        raise NotImplementedError

    def get_chat_model(self, llm_model: str, **kwargs) -> BaseChatModel:
        raise NotImplementedError

    def transcribe_audio(self, audio: BytesIO) -> str:
        raise NotImplementedError

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        raise NotImplementedError

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict[str, BaseModel] = None) -> list:
        raise NotImplementedError

    def get_output_parser(self):
        return self._default_parser

    def _default_parser(
        self, output: AIMessage, session: ExperimentSession, include_citations: bool = True
    ) -> LlmChatResponse:
        """
        Default parser for LLM outputs. Supports various formats including strings, dicts, and lists. This parser
        also extracts cited and generated files from annotations if present and handles deduplication of text entries
        (in rare cases).
        """
        final_text = output.text
        cited_file_ids_remote = []
        cited_file_ids = []
        generated_files: list[File] = []

        # Annotations etc are stored in content blocks:
        # https://docs.langchain.com/oss/python/langchain/messages#content-block-reference
        for content_block in output.content_blocks:
            # Uploaded files
            annotation_entries = content_block.get("annotations", [])
            if include_citations:
                # Cited files
                external_ids = self.get_cited_file_ids(annotation_entries)
                cited_file_ids_remote.extend(external_ids)

            # Generated files
            generated_files.extend(
                self.retrieve_generated_files_from_service_provider(annotation_entries, session.team_id)
            )

            # Replace generated file links with actual file download links
            for generated_file in generated_files:
                final_text = self.replace_file_links(text=final_text, file=generated_file, session=session)

        cited_file_ids.extend(extract_file_ids_from_ocs_citations(final_text))

        cited_files = []
        if include_citations:
            cited_files = File.objects.filter(
                Q(external_id__in=cited_file_ids_remote) | Q(id__in=cited_file_ids), team_id=session.team_id
            ).all()

        parsed_output = LlmChatResponse(
            text=final_text, cited_files=set(cited_files), generated_files=set(generated_files)
        )
        return parsed_output

    def get_remote_index_manager(self, index_id: str = None) -> IndexManager:
        raise NotImplementedError

    def get_local_index_manager(self, embedding_model_name: str) -> IndexManager:
        raise NotImplementedError

    def create_remote_index(self, name: str, file_ids: list = None) -> str:
        """
        Create a new vector store at the remote index service.

        Args:
            name: The name to assign to the new vector store.
            file_ids: Optional list of remote file IDs to initially associate with the vector store.

        Returns:
            str: The unique identifier of the newly created vector store.
        """
        raise NotImplementedError

    def get_cited_file_ids(self, annotation_entries: list[dict]) -> list[str]:
        return []

    def retrieve_generated_files_from_service_provider(
        self, annotation_entries: list[dict], team_id: int
    ) -> list[File]:
        return []

    def replace_file_links(self, text: str, file: File, session: ExperimentSession) -> str:
        """
        Replace file links in the text with actual download links.
        """
        return text


class OpenAIGenericService(LlmService):
    openai_api_key: str
    openai_api_base: str

    def get_chat_model(self, llm_model: str, **kwargs) -> BaseChatModel:
        from langchain_openai.chat_models import ChatOpenAI

        model_kwargs = self._get_model_kwargs(**kwargs)
        if "temperature" in model_kwargs and llm_model.startswith(("o3", "o4", "gpt-5", "o1")):
            # Remove the temperature parameter for custom reasoning models
            model_kwargs.pop("temperature")

        model = ChatOpenAI(model=llm_model, **model_kwargs, use_responses_api=True)
        try:
            model.get_num_tokens_from_messages([HumanMessage("Hello")])
        except Exception:
            # fallback if the model is not available for encoding
            match llm_model:
                case True if "gpt-4o" in llm_model:
                    model.tiktoken_model_name = "gpt-4o"
                case True if "gpt-3.5" in llm_model:
                    model.tiktoken_model_name = "gpt-3.5-turbo"
                case _:
                    model.tiktoken_model_name = "gpt-4"
        return model

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(OpenAITokenCounter(model))

    def _get_model_kwargs(self, **kwargs) -> dict:
        if effort := kwargs.pop("effort", None):
            kwargs["reasoning"] = {"effort": effort}

        return {"openai_api_key": self.openai_api_key, "openai_api_base": self.openai_api_base, **kwargs}

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict[str, BaseModel] = None) -> list:
        return []

    def get_cited_file_ids(self, annotation_entries: list[dict]) -> list[str]:
        """Returns the file ids from the annotation entries of type file_citation

        Expected format for annotation_entries:
        [{"type: "citation", "extras": {"file_id": "file-xxx"}}, ...]
        """
        external_ids = [
            entry.get("extras", {}).get("file_id") for entry in annotation_entries if entry["type"] == "citation"
        ]
        # Filter out None values (e.g., when citations contain URLs instead of file_ids)
        external_ids = [file_id for file_id in external_ids if file_id is not None]
        return detangle_file_ids(external_ids)

    def retrieve_generated_files_from_service_provider(
        self, annotation_entries: list[dict], team_id: int
    ) -> list[File]:
        """
        Create file records for all generated files in the output.

        Annotation entries for OpenAI generated files look like:
        [
            {"type": "container_file_citation", "file_id": "file-xxx", "container_id": "cont-xxx", ...}
        ]

        but Langchain transforms unknown annotation types into dicts with a "value" key and the type as
        `non_standard_annotation`. See
        https://github.com/langchain-ai/langchain/blob/master/libs/core/langchain_core/messages/block_translators/openai.py#L602-L607
        so we expect to find entries like this:
        [
            {
                "type": "non_standard_annotation",
                "value": {"type": "container_file_citation", "file_id": "file-xxx", "container_id": "cont-xxx", ...}
            }
        ]
        """
        generated_files = []

        for entry in annotation_entries:
            # We know to look for container_file_citation entries in entries for type = non_standard_annotation
            if entry.get("type", "") != "non_standard_annotation":
                continue

            entry = entry.get("value", entry)
            if entry.get("type") != "container_file_citation":
                continue

            file_external_id = entry["file_id"]
            container_id = entry["container_id"]

            # Retrieve file content from OpenAI container
            try:
                # Use direct HTTP request for container files until the library supports it and it is working with
                # langchain

                # Create new File instance
                file_contents = get_openai_container_file_contents(
                    container_id,
                    openai_file_id=file_external_id,
                    openai_api_key=self.openai_api_key,
                    openai_organization=self.openai_organization,
                )
                new_file = File.from_external_source(
                    filename=entry["filename"],
                    external_file=file_contents,
                    external_id=file_external_id,
                    external_source="openai",
                    team_id=team_id,
                )
                generated_files.append(new_file)

            except Exception:
                logger.exception(f"Failed to retrieve file {file_external_id} from OpenAI")
                continue

        return generated_files

    def replace_file_links(self, text: str, file: File, session: ExperimentSession) -> str:
        """
        Replace file links in the text with actual download links.
        """
        pattern = rf"\(sandbox:/mnt/data/{re.escape(file.name)}\)"
        replacement = f"({file.download_link(session.id)})"
        return re.sub(pattern, replacement, text)


class OpenAILlmService(OpenAIGenericService):
    openai_api_base: str = None
    openai_organization: str = None

    def _get_model_kwargs(self, **kwargs) -> dict:
        return {
            **super()._get_model_kwargs(**kwargs),
            "openai_organization": self.openai_organization,
        }

    def get_raw_client(self) -> OpenAI:
        return OpenAI(api_key=self.openai_api_key, organization=self.openai_organization, base_url=self.openai_api_base)

    def get_assistant(self, assistant_id: str, as_agent=False):
        from apps.service_providers.llm_service.openai_assistant import OpenAIAssistantRunnable

        return OpenAIAssistantRunnable(assistant_id=assistant_id, as_agent=as_agent, client=self.get_raw_client())

    def transcribe_audio(self, audio: BytesIO) -> str:
        transcript = self.get_raw_client().audio.transcriptions.create(
            model="whisper-1",
            file=audio,
        )
        return transcript.text

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict[str, BaseModel] = None) -> list:
        tools = []
        for tool_name in built_in_tools:
            if tool_name == "web-search":
                tools.append(OpenAIBuiltinTool({"type": "web_search_preview"}))
            elif tool_name == "code-execution":
                tools.append(OpenAIBuiltinTool({"type": "code_interpreter", "container": {"type": "auto"}}))
            else:
                raise ValueError(f"Unsupported built-in tool for openai: '{tool_name}'")
        return tools

    def get_remote_index_manager(self, index_id: str = None) -> IndexManager:
        from apps.service_providers.llm_service.index_managers import OpenAIRemoteIndexManager

        return OpenAIRemoteIndexManager(client=self.get_raw_client(), index_id=index_id)

    def get_local_index_manager(self, embedding_model_name: str) -> IndexManager:
        from apps.service_providers.llm_service.index_managers import OpenAILocalIndexManager

        return OpenAILocalIndexManager(api_key=self.openai_api_key, embedding_model_name=embedding_model_name)

    def create_remote_index(self, name: str, file_ids: list = None) -> str:
        file_ids = file_ids or NOT_GIVEN
        vector_store = self.get_raw_client().vector_stores.create(name=name, file_ids=file_ids)
        return vector_store.id


class AzureLlmService(LlmService):
    openai_api_key: str
    openai_api_base: str
    openai_api_version: str

    def get_chat_model(self, llm_model: str, **kwargs) -> BaseChatModel:
        from langchain_openai.chat_models import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_endpoint=self.openai_api_base,
            openai_api_version=self.openai_api_version,
            openai_api_key=self.openai_api_key,
            deployment_name=llm_model,
            **kwargs,
        )

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(OpenAITokenCounter(model))

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict[str, BaseModel] = None) -> list:
        return []


class AnthropicLlmService(LlmService):
    anthropic_api_key: str
    anthropic_api_base: str

    def get_chat_model(self, llm_model: str, **kwargs) -> BaseChatModel:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            anthropic_api_key=self.anthropic_api_key,
            anthropic_api_url=self.anthropic_api_base,
            model=llm_model,
            **self._get_model_kwargs(**kwargs),
        )

    def _get_model_kwargs(self, **kwargs) -> dict:
        budget_tokens = kwargs.pop("budget_tokens", 1024)
        if kwargs.pop("thinking", False):
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget_tokens,
            }

        return kwargs

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(AnthropicTokenCounter(model, self.anthropic_api_key))

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict[str, BaseModel] = None) -> list:
        config = config or {}
        tools = []
        for tool_name in built_in_tools:
            if tool_name == "web-search":
                tool = AnthropicBuiltinTool(
                    type="web_search_20250305",
                    name="web_search",
                    max_uses=5,
                )
                if tool_config := config.get(tool_name):
                    tool.update(tool_config.model_dump())
                tools.append(tool)
            else:
                raise ValueError(f"Unsupported built-in tool for anthropic: '{tool_name}'")
        return tools

    def get_output_parser(self):
        return parse_output_for_anthropic


class DeepSeekLlmService(LlmService):
    deepseek_api_key: str
    deepseek_api_base: str

    def get_chat_model(self, llm_model: str, **kwargs) -> BaseChatModel:
        from langchain_openai.chat_models import ChatOpenAI

        return ChatOpenAI(
            model=llm_model, openai_api_key=self.deepseek_api_key, openai_api_base=self.deepseek_api_base, **kwargs
        )

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(OpenAITokenCounter(model))

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict[str, BaseModel] = None) -> list:
        return []


class GoogleLlmService(LlmService):
    google_api_key: str

    def get_chat_model(self, llm_model: str, **kwargs) -> BaseChatModel:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=llm_model, google_api_key=self.google_api_key, **kwargs)

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(GeminiTokenCounter(model, self.google_api_key))

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict[str, BaseModel] = None) -> list:
        return []
        # Commenting it for now until we fix it
        # otherwise gemini would not work if code execution or web search is selected in the node
        # tools = []
        # for tool_name in built_in_tools:
        #     if tool_name == "web-search":
        #         tools.append(GenAITool(google_search={}))
        #     elif tool_name == "code-execution":
        #         tools.append(GenAITool(code_execution={}))
        #     else:
        #         raise ValueError(f"Unsupported built-in tool for gemini: '{tool_name}'")
        # return tools

    def get_local_index_manager(self, embedding_model_name: str) -> IndexManager:
        from apps.service_providers.llm_service.index_managers import GoogleLocalIndexManager

        return GoogleLocalIndexManager(api_key=self.google_api_key, embedding_model_name=embedding_model_name)


class GoogleVertexAILlmService(LlmService):
    credentials_json: dict
    location: str = "global"
    api_transport: Literal["grpc", "rest"] = "grpc"

    def get_chat_model(self, llm_model: str, **kwargs) -> BaseChatModel:
        from langchain_google_vertexai import ChatVertexAI

        return ChatVertexAI(
            model=llm_model,
            credentials=self.credentials,
            location=self.location,
            api_transport=self.api_transport,
            **kwargs,
        )

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        chat_model = self.get_chat_model(llm_model=model)
        token_counter = GoogleVertexAITokenCounter(chat_model)
        return TokenCountingCallbackHandler(token_counter)

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict[str, BaseModel] | None = None) -> list:
        return []

    @cached_property
    def credentials(self):
        from google.oauth2 import service_account

        try:
            return service_account.Credentials.from_service_account_info(self.credentials_json)
        except (KeyError, ValueError) as e:
            raise ServiceProviderConfigError(self._type, f"Invalid service account credentials: {e}") from e
