from io import BytesIO

import openai
import pydantic
from langchain.chat_models import AzureChatOpenAI, ChatOpenAI
from langchain.chat_models.base import BaseChatModel


class LlmService(pydantic.BaseModel):
    _type: str
    supports_transcription: bool = False

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        raise NotImplementedError

    def transcribe_audio(self, audio: BytesIO) -> str:
        raise NotImplementedError


class OpenAILlmService(LlmService):
    _type = "openai"
    supports_transcription: bool = True

    openai_api_key: str
    openai_api_base: str = None
    openai_organization: str = None

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        return ChatOpenAI(
            model=llm_model,
            temperature=temperature,
            openai_api_key=self.openai_api_key,
            openai_api_base=self.openai_api_base,
            openai_organization=self.openai_organization,
        )

    def transcribe_audio(self, audio: BytesIO) -> str:
        transcript = openai.Audio.transcribe(
            model="whisper-1",
            file=audio,
            api_key=self.openai_api_key,
            api_base=self.openai_api_base,
            organization=self.openai_organization,
        )
        return transcript["text"]


class AzureLlmService(LlmService):
    _type = "openai"

    openai_api_key: str
    openai_api_base: str

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        return AzureChatOpenAI(
            model=llm_model,
            temperature=temperature,
            openai_api_key=self.openai_api_key,
            openai_api_base=self.openai_api_base,
        )
