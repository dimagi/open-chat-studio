from io import BytesIO
from typing import Type

import openai
import pydantic
from langchain.chat_models import ChatOpenAI
from langchain.chat_models.base import BaseChatModel
from langchain.llms import AzureOpenAI


class LlmService(pydantic.BaseModel):
    _type: str
    _chat_model_cls: Type[BaseChatModel]
    supports_transcription: bool = False

    def get_chat_model(self, llm_model: str, temperature: float) -> BaseChatModel:
        return self._chat_model_cls(model=llm_model, temperature=temperature)

    def transcribe_audio(self, audio: BytesIO) -> str:
        raise NotImplementedError


class OpenAILlmService(LlmService):
    _type = "openai"
    _chat_model_cls = ChatOpenAI
    supports_transcription: bool = True

    openai_api_key: str
    openai_api_base: str = None
    openai_organization: str = None

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
    _chat_model_cls = AzureOpenAI

    openai_api_key: str
    openai_api_base: str
