from io import BytesIO
from typing import ClassVar

import pydantic
from langchain.agents.openai_assistant import OpenAIAssistantRunnable
from langchain.chat_models.base import BaseChatModel
from langchain_community.chat_models import ChatAnthropic
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseLanguageModel
from langchain_openai.chat_models import AzureChatOpenAI, ChatOpenAI
from openai import OpenAI
from openai._base_client import SyncAPIClient

from apps.service_providers.llm_service.callbacks import TokenCountingCallbackHandler


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


class OpenAILlmService(LlmService):
    _type = "openai"

    openai_api_key: str
    openai_api_base: str = None
    openai_organization: str = None

    def get_raw_client(self) -> OpenAI:
        return OpenAI(api_key=self.openai_api_key, organization=self.openai_organization, base_url=self.openai_api_base)

    def get_assistant(self, assistant_id: str, as_agent=False):
        return OpenAIAssistantRunnable(assistant_id=assistant_id, as_agent=as_agent, client=self.get_raw_client())

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        return ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            openai_api_key=self.openai_api_key,
            openai_api_base=self.openai_api_base,
            openai_organization=self.openai_organization,
        )

    def transcribe_audio(self, audio: BytesIO) -> str:
        transcript = self.get_raw_client().audio.transcriptions.create(
            model="whisper-1",
            file=audio,
        )
        return transcript.text

    def get_callback_handler(self, llm_model: BaseLanguageModel) -> BaseCallbackHandler:
        from langchain_community.callbacks import OpenAICallbackHandler

        return OpenAICallbackHandler()


class AzureLlmService(LlmService):
    _type = "openai"

    openai_api_key: str
    openai_api_base: str
    openai_api_version: str

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        return AzureChatOpenAI(
            azure_endpoint=self.openai_api_base,
            openai_api_version=self.openai_api_version,
            openai_api_key=self.openai_api_key,
            deployment_name=llm_model,
            temperature=temperature,
        )


class AnthropicLlmService(LlmService):
    _type = "anthropic"

    anthropic_api_key: str
    anthropic_api_base: str

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        return ChatAnthropic(
            anthropic_api_key=self.anthropic_api_key,
            anthropic_api_url=self.anthropic_api_base,
            model=llm_model,
            temperature=temperature,
        )
