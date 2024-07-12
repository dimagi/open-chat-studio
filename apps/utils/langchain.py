import dataclasses
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock
from unittest.mock import patch

from langchain.agents.openai_assistant.base import OpenAIAssistantFinish, OutputType
from langchain_community.chat_models.fake import FakeListChatModel
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableConfig, RunnableSerializable
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from apps.accounting.usage import BaseUsageRecorder, UsageScope
from apps.service_providers.llm_service import LlmService
from apps.service_providers.llm_service.callbacks import UsageCallbackHandler
from apps.service_providers.llm_service.token_counters import TokenCounter
from apps.service_providers.service_usage import UsageMixin
from apps.teams.models import BaseTeamModel


class FakeLlm(FakeListChatModel):
    """Extension of the FakeListChatModel that allows mocking of the token counts."""

    token_counts: list = []
    token_i: int = 0
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
        if isinstance(response, BaseMessage):
            yield ChatGenerationChunk(message=response)
        else:
            for c in response:
                yield ChatGenerationChunk(message=AIMessageChunk(content=c))

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

    def bind_tools(self, tools):
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools])


class FakeUsageRecorder(BaseUsageRecorder):
    def __init__(self):
        super().__init__()
        self.totals = Counter()

    def _get_scope(self, source_object: BaseTeamModel, metadata: dict = None):
        return UsageScope(
            service_object=mock.Mock(team_id=source_object.team_id),
            source_object=source_object,
            metadata=metadata or {},
        )

    def commit_and_clear(self):
        for usage in self.get_batch():
            self.totals[usage.type] += usage.value
        self.usage = []


class FakeAssistant(RunnableSerializable[dict, OutputType]):
    responses: list
    i: int = 0

    def invoke(self, input: dict, config: RunnableConfig | None = None) -> OutputType:
        response = self._get_next_response()
        if isinstance(response, BaseException):
            raise response
        return OpenAIAssistantFinish(return_values={"output": response}, log="", thread_id="123", run_id="456")

    def _get_next_response(self):
        response = self.responses[self.i]
        if self.i < len(self.responses) - 1:
            self.i += 1
        else:
            self.i = 0
        return response


class FakeLlmService(LlmService, UsageMixin):
    llm: Any
    assistant: Any = Field(default_factory=lambda: FakeAssistant(responses=[]))
    usage_recorder: BaseUsageRecorder = Field(default_factory=FakeUsageRecorder)

    class Config:
        arbitrary_types_allowed = True

    def get_chat_model(self, llm_model: str, temperature: float):
        return self.llm

    def get_assistant(self, assistant_id: str, as_agent=False):
        return self.assistant


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


@contextmanager
def mock_experiment_llm_service(responses: list, token_counts: list[int] = None):
    usage_recorder = FakeUsageRecorder()
    llm = FakeLlm(
        responses=responses,
        token_counts=token_counts or [0],
        callbacks=[
            UsageCallbackHandler(usage_recorder, FakeTokenCounter()),
        ],
    )
    service = FakeLlmService(llm=llm, usage_recorder=usage_recorder, assistant=FakeAssistant(responses=responses))

    def fake_llm_service(self):
        return service

    with (
        patch("apps.experiments.models.Experiment.get_llm_service", new=fake_llm_service),
        patch("apps.assistants.models.OpenAiAssistant.get_llm_service", new=fake_llm_service),
    ):
        yield service
