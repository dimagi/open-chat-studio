from io import BytesIO
from typing import ClassVar

import pydantic
from langchain.chat_models import AzureChatOpenAI, ChatOpenAI
from langchain.chat_models.base import BaseChatModel
from openai import OpenAI
from openai._base_client import SyncAPIClient


class LlmService(pydantic.BaseModel):
    _type: ClassVar[str]
    supports_transcription: ClassVar[bool] = False
    supports_assistant: ClassVar[bool] = False

    def get_raw_client(self) -> SyncAPIClient:
        raise NotImplementedError

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        raise NotImplementedError

    def transcribe_audio(self, audio: BytesIO) -> str:
        raise NotImplementedError


class OpenAILlmService(LlmService):
    _type = "openai"
    supports_transcription = True
    supports_assistant = True

    openai_api_key: str
    openai_api_base: str = None
    openai_organization: str = None

    def get_raw_client(self) -> OpenAI:
        return OpenAI(api_key=self.openai_api_key, organization=self.openai_organization, base_url=self.openai_api_base)

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
