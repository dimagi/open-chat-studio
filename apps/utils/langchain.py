import dataclasses
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock
from unittest.mock import patch

from langchain.agents.openai_assistant.base import OpenAIAssistantFinish, OutputType
from langchain_community.chat_models import FakeListChatModel
from langchain_core.callbacks import BaseCallbackHandler, CallbackManagerForLLMRun
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, BaseMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.prompt_values import ChatPromptValue
from langchain_core.runnables import RunnableConfig, RunnableLambda, RunnableSerializable
from langchain_core.utils.function_calling import convert_to_openai_tool
from openai import OpenAI
from pydantic import ConfigDict

from apps.service_providers.llm_service import LlmService
from apps.service_providers.llm_service.callbacks import TokenCountingCallbackHandler
from apps.service_providers.llm_service.main import OpenAIAssistantRunnable
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

        if isinstance(output, dict):
            is_structured_output = any(t.get("type") == "function" for t in kwargs.get("tools", []))

            if is_structured_output:
                tool_name = "function"
                if "tools" in kwargs and kwargs["tools"]:
                    tool_name = kwargs["tools"][0]["function"]["name"]

                message = AIMessage(
                    content="",
                    additional_kwargs={
                        "tool_calls": [
                            {
                                "id": f"call_{uuid.uuid4().hex[:12]}",
                                "function": {"arguments": json.dumps(output), "name": tool_name},
                                "type": "function",
                            }
                        ]
                    },
                )
            else:
                message = AIMessage(content=json.dumps(output))
        elif isinstance(output, BaseMessage):
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


def with_structured_output(self, schema) -> RunnableSerializable:
    is_router_schema = hasattr(schema, "__annotations__") and "route" in schema.__annotations__

    def _structured_output_handler(input_value, *args, **kwargs):
        if isinstance(input_value, ChatPromptValue):
            messages = input_value.messages
        else:
            messages = [input_value]

        self._call(messages, *args, **kwargs)

        if is_router_schema:
            user_msg = ""
            for msg in messages:
                if msg.type == "human":
                    user_msg = msg.content.lower()
                    break

            route_values = schema.__fields__["route"].annotation.__args__

            selected_route = route_values[0]  # Default to first route
            for route in route_values:
                if route.lower() == user_msg.lower():
                    selected_route = route
                    break

            return schema(route=selected_route)

        # For non-router schemas, just return a JSON string
        return {"output": "default_output"}

    return RunnableLambda(_structured_output_handler)


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

    def get_chat_model(self, llm_model: str, temperature: float):
        return self.llm

    def get_assistant(self, assistant_id: str, as_agent=False):
        client = OpenAI(api_key="fake_key", base_url="https://fake.com")
        return OpenAIAssistantRunnable(assistant_id=assistant_id, as_agent=as_agent, client=client)

    def get_callback_handler(self, model: str) -> BaseCallbackHandler:
        return TokenCountingCallbackHandler(self.token_counter)


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

        user_message = messages[-1].content
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
        patch("apps.experiments.models.Experiment.get_llm_service", new=fake_llm_service),
        patch("apps.assistants.models.OpenAiAssistant.get_assistant", new=fake_get_assistant),
        patch("apps.service_providers.models.LlmProvider.get_llm_service", new=fake_llm_service),
    ):
        yield service


def build_fake_llm_service(responses, token_counts, fake_llm=None):
    fake_llm = fake_llm or FakeLlmSimpleTokenCount(responses=responses)
    return FakeLlmService(llm=fake_llm, token_counter=FakeTokenCounter(token_counts=token_counts))


def build_fake_llm_echo_service(token_counts=None, include_system_message=True):
    if token_counts is None:
        token_counts = [0]
    llm = FakeLlmEcho(include_system_message=include_system_message)
    return FakeLlmService(llm=llm, token_counter=FakeTokenCounter(token_counts=token_counts))
