"""Deterministic stand-ins for external calls, so benchmarks measure OCS work only.

The seam is ``LlmProvider.get_llm_service``: everything after it (prompt assembly, token counting,
history, LangGraph execution) stays on the measured path, and only the provider round-trip is replaced.
"""

import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseLanguageModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

from apps.utils.tests.langchain import FakeLlmService

DEFAULT_RESPONSE = "This is a canned benchmark response."


class BenchmarkChatModel(FakeListChatModel):
    """Replays ``responses`` in order, wrapping around, with an optional fixed delay per call.

    With a single response and no delay this is the zero-latency mock. With responses captured from a
    real provider (see ``recorded_replay``) it is the recorded-replay mode. Calls are counted rather
    than stored so memory does not grow over thousands of benchmark rounds.
    """

    responses: list = [DEFAULT_RESPONSE]
    delay_seconds: float = 0.0
    call_count: int = 0

    def _next_response(self) -> str | BaseMessage:
        response = self.responses[self.call_count % len(self.responses)]
        self.call_count += 1
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return response

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = self._next_response()
        message = response if isinstance(response, BaseMessage) else AIMessage(content=response)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages: list[BaseMessage], *args, **kwargs) -> Iterator[ChatGenerationChunk]:
        response = self._next_response()
        if isinstance(response, AIMessageChunk):
            yield ChatGenerationChunk(message=response)
        elif isinstance(response, BaseMessage):
            yield ChatGenerationChunk(message=AIMessageChunk(content=response.content, id=response.id))
        else:
            for chunk in response.split(" "):
                yield ChatGenerationChunk(message=AIMessageChunk(content=f"{chunk} "))

    def get_num_tokens(self, text: str) -> int:
        return len(text.split())

    def get_num_tokens_from_messages(self, messages: list, *args, **kwargs) -> int:
        return BaseLanguageModel.get_num_tokens_from_messages(self, messages)

    def bind_tools(self, tools, *args, **kwargs):
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools])


def zero_latency(response: str = DEFAULT_RESPONSE) -> BenchmarkChatModel:
    return BenchmarkChatModel(responses=[response])


def recorded_replay(responses: Sequence[str | BaseMessage]) -> BenchmarkChatModel:
    """Replay captured provider responses (text, tool calls, streaming chunks) without a network call."""
    return BenchmarkChatModel(responses=list(responses))


def fixed_delay(delay_seconds: float, response: str = DEFAULT_RESPONSE) -> BenchmarkChatModel:
    """Constant artificial latency, for concurrency and pool-pressure scenarios."""
    return BenchmarkChatModel(responses=[response], delay_seconds=delay_seconds)


@contextmanager
def fake_llm_provider(llm: BenchmarkChatModel | None = None):
    """Route every ``LlmProvider.get_llm_service`` call to a fake service wrapping ``llm``."""
    service = FakeLlmService(llm=llm or zero_latency())
    with patch("apps.service_providers.models.LlmProvider.get_llm_service", new=lambda self: service):
        yield service
