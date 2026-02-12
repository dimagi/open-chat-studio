import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock
from unittest.mock import patch

from langchain_classic.agents.openai_assistant.base import OpenAIAssistantFinish, OutputType
from langchain_community.chat_models import FakeListChatModel
from langchain_core.callbacks import BaseCallbackHandler, CallbackManagerForLLMRun
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, BaseMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableConfig, RunnableSerializable
from langchain_core.utils.function_calling import convert_to_openai_tool
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, create_model

from apps.service_providers.llm_service import LlmService, OpenAIGenericService
from apps.service_providers.llm_service.callbacks import TokenCountingCallbackHandler
from apps.service_providers.llm_service.openai_assistant import OpenAIAssistantRunnable
from apps.service_providers.llm_service.token_counters import TokenCounter


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

    def _call(self, messages: list[BaseMessage], *args, **kwargs) -> str | BaseMessage:
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


@dataclasses.dataclass
class FakeTokenCounter(TokenCounter):
    token_counts: list[int] = dataclasses.field(default_factory=lambda: [1])
    token_i: int = 0

    def get_tokens_from_text(self, text) -> int:
        token_counts = self.token_counts[self.token_i]
        if self.token_i < len(self.token_counts) - 1:
            self.token_i += 1
        else:
            self.token_i = 0
        return token_counts


class FakeLlmService(LlmService):
    llm: Any
    token_counter: TokenCounter
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_chat_model(self, llm_model: str, **kwargs):
        return self.llm

    def get_assistant(self, assistant_id: str, as_agent=False):
        client = OpenAI(api_key="fake_key", base_url="https://fake.com")
        return OpenAIAssistantRunnable(assistant_id=assistant_id, as_agent=as_agent, client=client)

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(self.token_counter)

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict = None) -> list:
        return []


class FakeOpenAILlmService(OpenAIGenericService):
    llm: Any
    token_counter: TokenCounter
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def get_chat_model(self, llm_model: str, **kwargs):
        return self.llm

    def get_assistant(self, assistant_id: str, as_agent=False):
        client = OpenAI(api_key="fake_key", base_url="https://fake.com")
        return OpenAIAssistantRunnable(assistant_id=assistant_id, as_agent=as_agent, client=client)

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(self.token_counter)

    def attach_built_in_tools(self, built_in_tools: list[str], config: dict = None) -> list:
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


class FakeAssistant(RunnableSerializable[dict, OutputType]):
    responses: list
    i: int = 0

    def invoke(self, input: dict, config: RunnableConfig | None = None) -> OutputType:
        response = self._get_next_response()
        if isinstance(response, BaseException):
            raise response
        return OpenAIAssistantFinish(
            return_values={"output": response, "run_id": "456"}, log="", thread_id="123", run_id="456"
        )

    def _get_next_response(self):
        response = self.responses[self.i]
        if self.i < len(self.responses) - 1:
            self.i += 1
        else:
            self.i = 0
        return response


class FakeLlmSimpleTokenCount(FakeLlm):
    def get_num_tokens(self, text: str) -> int:
        return len(text.split())

    def get_num_tokens_from_messages(self, messages: list) -> int:
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
def mock_llm(responses: list[Any], token_counts: list[int] = None):
    service = build_fake_llm_service(responses=responses, token_counts=token_counts)

    def fake_llm_service(self):
        return service

    assistant = FakeAssistant(responses=responses)

    def fake_get_assistant(self):
        return assistant

    with (
        patch("apps.assistants.models.OpenAiAssistant.get_assistant", new=fake_get_assistant),
        patch("apps.service_providers.models.LlmProvider.get_llm_service", new=fake_llm_service),
    ):
        yield service


def build_fake_llm_service(responses, token_counts, fake_llm=None, llm_service_class=FakeLlmService):
    fake_llm = fake_llm or FakeLlmSimpleTokenCount(responses=responses)
    return llm_service_class(llm=fake_llm, token_counter=FakeTokenCounter(token_counts=token_counts))


def build_fake_llm_echo_service(token_counts=None, include_system_message=True):
    if token_counts is None:
        token_counts = [0]
    llm = FakeLlmEcho(include_system_message=include_system_message)
    return FakeLlmService(llm=llm, token_counter=FakeTokenCounter(token_counts=token_counts))


def dict_to_json_schema(data: dict) -> type[BaseModel]:
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

    Model.description = ""
    return Model
