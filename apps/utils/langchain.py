from contextlib import contextmanager
from typing import Any, List, Optional

from langchain.chat_models import FakeListChatModel
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage

from apps.service_providers.llm_service import LlmService


class FakeLlm(FakeListChatModel):
    """Extension of the FakeListChatModel that allows mocking of the token counts."""

    token_counts: list
    token_i: int = 0
    calls: List = []

    def _call(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        self.calls.append(messages)
        return super()._call(messages, stop, run_manager, **kwargs)

    def get_num_tokens_from_messages(self, messages: list) -> int:
        token_counts = self.token_counts[self.token_i]
        if self.token_i < len(self.token_counts) - 1:
            self.token_i += 1
        else:
            self.token_i = 0
        return token_counts

    def get_num_tokens(self, text: str) -> int:
        return self.get_num_tokens_from_messages([])


class FakeLlmService(LlmService):
    llm: Any

    def get_chat_model(self, llm_model: str, temperature: float):
        return self.llm


@contextmanager
def mock_experiment_llm(experiment, responses: list[str], token_counts: list[int] = None):
    original = experiment.llm_provider.get_llm_service
    experiment.llm_provider.get_llm_service = lambda: FakeLlmService(
        llm=FakeLlm(responses=responses, token_counts=token_counts or [0])
    )
    try:
        yield
    finally:
        experiment.llm_provider.get_llm_service = original
