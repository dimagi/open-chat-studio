from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

from langchain_community.chat_models import FakeListChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk

from apps.service_providers.llm_service import LlmService


class FakeLlm(FakeListChatModel):
    """Extension of the FakeListChatModel that allows mocking of the token counts."""

    token_counts: list
    token_i: int = 0
    calls: list = []

    def _call(self, messages: list[BaseMessage], *args, **kwargs) -> str:
        self.calls.append(mock.call(messages, *args, **kwargs))
        return super()._call(messages, *args, **kwargs)

    def _stream(self, messages: list[BaseMessage], *args, **kwargs) -> Iterator[ChatGenerationChunk]:
        self.calls.append(mock.call(messages, *args, **kwargs))
        return super()._stream(messages, *args, **kwargs)

    def get_num_tokens_from_messages(self, messages: list) -> int:
        token_counts = self.token_counts[self.token_i]
        if self.token_i < len(self.token_counts) - 1:
            self.token_i += 1
        else:
            self.token_i = 0
        return token_counts

    def get_num_tokens(self, text: str) -> int:
        return self.get_num_tokens_from_messages([])

    def get_calls(self):
        return self.calls

    def get_call_messages(self):
        return [call[1][0] for call in self.calls]


class FakeLlmService(LlmService):
    llm: Any

    def get_chat_model(self, llm_model: str, temperature: float):
        return self.llm


@contextmanager
def mock_experiment_llm(experiment, responses: list[str], token_counts: list[int] = None):
    original = experiment.get_llm_service
    experiment.get_llm_service = lambda: FakeLlmService(
        llm=FakeLlm(responses=responses, token_counts=token_counts or [0])
    )
    try:
        yield
    finally:
        experiment.get_llm_service = original
