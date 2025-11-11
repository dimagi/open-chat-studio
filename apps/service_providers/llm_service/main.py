from __future__ import annotations

import contextlib
import logging
import re
from io import BytesIO
from time import sleep
from typing import TYPE_CHECKING, Any

import pydantic
from django.db.models import Q
from langchain.agents.openai_assistant import OpenAIAssistantRunnable as BrokenOpenAIAssistantRunnable
from langchain_anthropic import ChatAnthropic
from langchain_core.callbacks import BaseCallbackHandler, CallbackManager, dispatch_custom_event
from langchain_core.language_models import BaseChatModel
from langchain_core.load import dumpd
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig, ensure_config
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai.chat_models import AzureChatOpenAI, ChatOpenAI
from openai import NOT_GIVEN, OpenAI
from openai._base_client import SyncAPIClient
from pydantic import BaseModel

from apps.experiments.models import ExperimentSession
from apps.files.models import File
from apps.service_providers.llm_service.callbacks import TokenCountingCallbackHandler
from apps.service_providers.llm_service.datamodels import LlmChatResponse
from apps.service_providers.llm_service.parsers import parse_output_for_anthropic
from apps.service_providers.llm_service.token_counters import (
    AnthropicTokenCounter,
    GeminiTokenCounter,
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


class OpenAIAssistantRunnable(BrokenOpenAIAssistantRunnable):
    # This is a temporary solution to fix langchain's compatability with the assistants v2 API. This code is
    # copied from:
    # `https://github.com/langchain-ai/langchain/blob/54adcd9e828e24bb24b2055f410137aca6a12834/libs/langchain/
    # langchain/agents/openai_assistant/base.py#L256`.
    # and updated so that the thread API gets an `attachments` key instead of the previous `file_ids` key.
    # TODO: Here's a PR that tries to fix it in LangChain: https://github.com/langchain-ai/langchain/pull/21484

    def invoke(self, input: dict, config: RunnableConfig | None = None):
        config = ensure_config(config)
        callback_manager = CallbackManager.configure(
            inheritable_callbacks=config.get("callbacks"),
            inheritable_tags=config.get("tags"),
            inheritable_metadata=config.get("metadata"),
        )
        run_manager = callback_manager.on_chain_start(dumpd(self), input, name=config.get("run_name"))
        try:
            # Being run within AgentExecutor and there are tool outputs to submit.
            if self.as_agent and input.get("intermediate_steps"):
                tool_outputs = self._parse_intermediate_steps(input["intermediate_steps"])
                run = self.client.beta.threads.runs.submit_tool_outputs(**tool_outputs)
            # Starting a new thread and a new run.
            elif "thread_id" not in input:
                thread = {
                    "messages": [
                        {
                            "role": "user",
                            "content": input["content"],
                            "attachments": input.get("attachments", {}),
                            "metadata": input.get("message_metadata"),
                        }
                    ],
                    "metadata": input.get("thread_metadata"),
                }
                run = self._create_thread_and_run(input, thread)
            # Starting a new run in an existing thread.
            elif "run_id" not in input:
                _ = self.client.beta.threads.messages.create(
                    input["thread_id"],
                    content=input["content"],
                    role="user",
                    attachments=input.get("attachments", {}),
                    metadata=input.get("message_metadata"),
                )
                run = self._create_run(input)
            # Submitting tool outputs to an existing run, outside the AgentExecutor
            # framework.
            else:
                run = self.client.beta.threads.runs.submit_tool_outputs(**input)
            with contextlib.suppress(RuntimeError):
                dispatch_custom_event(
                    "OpenAI Assistant Run Created",
                    {
                        "assistant_id": run.assistant_id,
                        "thread_id": run.thread_id,
                        "run_id": run.id,
                    },
                )
            run = self._wait_for_run(run.id, run.thread_id)
        except BaseException as e:
            run_manager.on_chain_error(e)
            raise e
        try:
            response = self._get_response(run)
        except BaseException as e:
            run_manager.on_chain_error(e, metadata=run.dict())
            raise e
        else:
            run_manager.on_chain_end(response)
            return response

    def _wait_for_run(self, run_id: str, thread_id: str, progress_states=("in_progress", "queued")) -> Any:
        in_progress = True
        while in_progress:
            run = self.client.beta.threads.runs.retrieve(run_id, thread_id=thread_id)
            in_progress = run.status in progress_states
            if in_progress:
                sleep(self.check_every_ms / 1000)
        return run


class LlmService(pydantic.BaseModel):
    _type: str
    supports_transcription: bool = False
    supports_assistants: bool = False

    def get_raw_client(self) -> SyncAPIClient:
        raise NotImplementedError

    def get_assistant(self, assistant_id: str, as_agent=False):
        raise NotImplementedError

    def get_chat_model(self, llm_model: str, temperature: float | None = None, **kwargs) -> BaseChatModel:
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
        self, llm_output, session: ExperimentSession, include_citations: bool = True
    ) -> LlmChatResponse:
        if isinstance(llm_output, dict):
            llm_outputs = llm_output.get("output", "")
        elif isinstance(llm_output, str):
            llm_outputs = llm_output
        elif isinstance(llm_output, list):
            # Normalize list outputs: support list[dict] and list[str]
            if all(isinstance(o, dict) for o in llm_output):
                llm_outputs = llm_output
            elif all(isinstance(o, str) for o in llm_output):
                llm_outputs = "\n".join(llm_output)
            else:
                raise TypeError("Unexpected mixed or unsupported list element types in llm_output")
        else:
            raise TypeError(f"Unexpected llm_output type: {type(llm_output).__name__}")

        final_text = ""
        cited_file_ids_remote = []
        cited_file_ids = []
        cited_files = []
        generated_files: list[File] = []
        if isinstance(llm_outputs, list):
            for output in llm_outputs:
                # Populate text
                final_text = "\n".join([final_text, output.get("text", "")]).strip()

                annotations = output.get("annotations", [])
                if include_citations:
                    external_ids = self.get_cited_file_ids(annotations)
                    cited_file_ids_remote.extend(external_ids)

                generated_files.extend(self.get_generated_files(annotations, session.team_id))

                # Replace generated file links with actual file download links
                for generated_file in generated_files:
                    final_text = self.replace_file_links(text=final_text, file=generated_file, session=session)
        else:
            final_text = llm_outputs
            # This path is followed when OCS citations are injected into the output
            cited_file_ids.extend(extract_file_ids_from_ocs_citations(llm_outputs))

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

    def get_generated_files(self, annotation_entries: list[dict], team_id: int) -> list[File]:
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
        model = ChatOpenAI(
            model=llm_model,
            **self._get_model_kwargs(**kwargs),
        )
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
        external_ids = [
            entry["file_id"] for entry in annotation_entries if "file_id" in entry and entry["type"] == "file_citation"
        ]
        return detangle_file_ids(external_ids)

    def get_generated_files(self, annotation_entries: list[dict], team_id: int) -> list[File]:
        """
        Create file records for all generated files in the output.
        """
        generated_files = []

        for entry in annotation_entries:
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
        return AzureChatOpenAI(
            azure_endpoint=self.openai_api_base,
            openai_api_version=self.openai_api_version,
            openai_api_key=self.openai_api_key,
            deployment_name=llm_model,
        )

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(OpenAITokenCounter(model))

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict[str, BaseModel] = None) -> list:
        return []


class AnthropicLlmService(LlmService):
    anthropic_api_key: str
    anthropic_api_base: str

    def get_chat_model(self, llm_model: str, **kwargs) -> BaseChatModel:
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
        return ChatOpenAI(
            model=llm_model,
            openai_api_key=self.deepseek_api_key,
            openai_api_base=self.deepseek_api_base,
        )

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(OpenAITokenCounter(model))

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict[str, BaseModel] = None) -> list:
        return []


class GoogleLlmService(LlmService):
    google_api_key: str

    def get_chat_model(self, llm_model: str, **kwargs) -> BaseChatModel:
        return ChatGoogleGenerativeAI(
            model=llm_model,
            google_api_key=self.google_api_key,
        )

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
