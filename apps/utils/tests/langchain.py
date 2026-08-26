from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock
from unittest.mock import patch

from langchain_community.chat_models import FakeListChatModel
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, BaseMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict

from apps.service_providers.llm_service import LlmService, OpenAIGenericService


class FakeLlm(FakeListChatModel):
    """Extension of the FakeListChatModel that allows mocking of the token counts."""

    calls: list = []

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        output = self._call(messages, stop=stop, run_manager=run_manager, **kwargs)
        if isinstance(output, BaseMessage):
            message = output
        else:
            message = AIMessage(content=output)

        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])

    def _call(self, messages: list[BaseMessage], *args, **kwargs) -> str | BaseMessage:  # ty: ignore[invalid-method-override]
        self.calls.append(mock.call(messages, *args, **kwargs))
        return super()._call(messages, *args, **kwargs)

    def _stream(self, messages: list[BaseMessage], *args, **kwargs) -> Iterator[ChatGenerationChunk]:
        response = self._call(messages, *args, **kwargs)
        if isinstance(response, BaseMessageChunk):
            yield ChatGenerationChunk(message=response)
        else:
            for c in response:
                yield ChatGenerationChunk(message=AIMessageChunk(content=c))

    def get_num_tokens(self, text: str) -> int:
        raise NotImplementedError

    def get_calls(self):
        return self.calls

    def get_call_messages(self):
        return [call[1][0] for call in self.calls]

    def bind_tools(self, tools, *args, **kwargs):
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools])


class FakeLlmService(LlmService):
    llm: Any
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_chat_model(self, llm_model: str, **kwargs):
        return self.llm

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict | None = None) -> list:
        return []


class FakeOpenAILlmService(OpenAIGenericService):
    llm: Any
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_chat_model(self, llm_model: str, **kwargs):
        return self.llm

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict | None = None) -> list:
        return []

    @property
    def openai_api_key(self) -> str:
        return "api_key-123"

    @property
    def openai_api_base(self) -> str:
        return "openai_api_base"

    @property
    def openai_organization(self) -> str:
        return "openai_organization"


class FakeLlmSimpleTokenCount(FakeLlm):
    def get_num_tokens(self, text: str) -> int:
        return len(text.split())

    def get_num_tokens_from_messages(self, messages: list) -> int:  # ty: ignore[invalid-method-override]
        return BaseLanguageModel.get_num_tokens_from_messages(self, messages)


class FakeLlmEcho(FakeLlmSimpleTokenCount):
    """Echos the input"""

    include_system_message: bool = True
    responses: list = []

    def _call(self, messages: list[BaseMessage], *args, **kwargs) -> str | BaseMessage:
        """Returns "{system_message} {user_message}" """
        self.calls.append(mock.call(messages, *args, **kwargs))
        user_message = messages[-1].text

        try:
            system_message = next(message.content for message in messages if message.type == "system")
        except StopIteration:
            return user_message

        return f"{system_message} {user_message}" if self.include_system_message else user_message


@contextmanager
def mock_llm(responses: list[Any]):
    service = build_fake_llm_service(responses=responses)

    def fake_llm_service(self):
        return service

    with patch("apps.service_providers.models.LlmProvider.get_llm_service", new=fake_llm_service):
        yield service


def build_fake_llm_service(responses, fake_llm=None, llm_service_class=FakeLlmService):
    fake_llm = fake_llm or FakeLlmSimpleTokenCount(responses=responses)
    return llm_service_class(llm=fake_llm)


def build_fake_llm_echo_service(include_system_message=True):
    llm = FakeLlmEcho(include_system_message=include_system_message)
    return FakeLlmService(llm=llm)
