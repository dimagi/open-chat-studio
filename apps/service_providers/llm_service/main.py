from io import BytesIO
from typing import ClassVar

import pydantic
from langchain.agents.openai_assistant import OpenAIAssistantRunnable as BrokenOpenAIAssistantRunnable
from langchain.chat_models.base import BaseChatModel
from langchain_anthropic import ChatAnthropic
from langchain_community.callbacks import OpenAICallbackHandler
from langchain_core.callbacks import BaseCallbackHandler, CallbackManager
from langchain_core.language_models import BaseLanguageModel
from langchain_core.load import dumpd
from langchain_core.runnables import RunnableConfig, ensure_config
from langchain_openai.chat_models import AzureChatOpenAI, ChatOpenAI
from openai import OpenAI
from openai._base_client import SyncAPIClient

from apps.service_providers.llm_service.callbacks import TokenCountingCallbackHandler, UsageCallbackHandler
from apps.service_providers.service_usage import UsageMixin


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


class LlmService(pydantic.BaseModel):
    _type: ClassVar[str]
    supports_transcription: bool = False
    supports_assistants: bool = False

    def get_raw_client(self) -> SyncAPIClient:
        raise NotImplementedError

    def get_assistant(self, assistant_id: str, as_agent=False):
        raise NotImplementedError

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        raise NotImplementedError

    def transcribe_audio(self, audio: BytesIO) -> str:
        raise NotImplementedError

    def get_callback_handler(self, llm_model: BaseLanguageModel) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(llm_model)


class OpenAILlmService(UsageMixin, LlmService):
    _type = "openai"

    openai_api_key: str
    openai_api_base: str = None
    openai_organization: str = None

    class Config:
        arbitrary_types_allowed = True

    def get_raw_client(self) -> OpenAI:
        return OpenAI(api_key=self.openai_api_key, organization=self.openai_organization, base_url=self.openai_api_base)

    def get_assistant(self, assistant_id: str, as_agent=False):
        return OpenAIAssistantRunnable(assistant_id=assistant_id, as_agent=as_agent, client=self.get_raw_client())

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        model = ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            openai_api_key=self.openai_api_key,
            openai_api_base=self.openai_api_base,
            openai_organization=self.openai_organization,
            callbacks=[
                UsageCallbackHandler(self.usage_recorder, OpenAICallbackHandler(), metadata={"model": llm_model})
            ],
        )
        if model._get_encoding_model()[0] == "cl100k_base":
            # fallback to gpt-4 if the model is not available for encoding
            model.tiktoken_model_name = "gpt-4"

        return model

    def transcribe_audio(self, audio: BytesIO) -> str:
        transcript = self.get_raw_client().audio.transcriptions.create(
            model="whisper-1",
            file=audio,
        )
        return transcript.text

    def get_callback_handler(self, llm_model: BaseLanguageModel) -> BaseCallbackHandler:
        from langchain_community.callbacks import OpenAICallbackHandler

        return OpenAICallbackHandler()


class AzureLlmService(LlmService, UsageMixin):
    _type = "openai"

    openai_api_key: str
    openai_api_base: str
    openai_api_version: str

    class Config:
        arbitrary_types_allowed = True

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        model = AzureChatOpenAI(
            azure_endpoint=self.openai_api_base,
            openai_api_version=self.openai_api_version,
            openai_api_key=self.openai_api_key,
            deployment_name=llm_model,
            temperature=temperature,
        )
        model.callbacks = [
            UsageCallbackHandler(
                self.usage_recorder, TokenCountingCallbackHandler(model), metadata={"model": llm_model}
            )
        ]
        return model


class AnthropicLlmService(LlmService, UsageMixin):
    _type = "anthropic"

    anthropic_api_key: str
    anthropic_api_base: str

    class Config:
        arbitrary_types_allowed = True

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        model = ChatAnthropic(
            anthropic_api_key=self.anthropic_api_key,
            anthropic_api_url=self.anthropic_api_base,
            model=llm_model,
            temperature=temperature,
        )
        model.callbacks = [
            UsageCallbackHandler(
                self.usage_recorder, TokenCountingCallbackHandler(model), metadata={"model": llm_model}
            )
        ]
        return model
