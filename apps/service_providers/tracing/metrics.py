from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler


@dataclass
class TraceMetrics:
    """Metrics collected during a single pipeline execution."""

    n_turns: int | None = None
    n_toolcalls: int | None = None
    n_total_tokens: int | None = None
    n_prompt_tokens: int | None = None
    n_completion_tokens: int | None = None


class MetricsCollector(UsageMetadataCallbackHandler):
    """Thread-safe accumulator for pipeline execution metrics.

    Collects turn counts, tool call counts, and token usage across all LLM
    calls in a pipeline execution. Token usage is inherited from
    UsageMetadataCallbackHandler, which reads AIMessage.usage_metadata — the
    standard location populated by modern LangChain chat-model integrations
    (including OpenAI's Responses API, where LLMResult.llm_output is None).

    Counter access is protected by a threading lock because LangGraph
    executes nodes in threads via DjangoSafeContextThreadPoolExecutor.
    """

    def __init__(self, start_time: float):
        super().__init__()
        self._counter_lock = threading.Lock()
        self._turns = 0
        self._toolcalls = 0

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        with self._counter_lock:
            self._turns += 1

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        with self._counter_lock:
            self._toolcalls += 1

    def get_metrics(self) -> TraceMetrics:
        """Return collected metrics, converting zero counts to None.

        Zero counts are converted to None to distinguish "no LLM calls happened"
        from "LLM calls happened but produced 0 tokens".
        """
        with self._counter_lock:
            turns = self._turns
            toolcalls = self._toolcalls
        with self._lock:
            prompt_tokens = sum(u.get("input_tokens", 0) for u in self.usage_metadata.values())
            completion_tokens = sum(u.get("output_tokens", 0) for u in self.usage_metadata.values())
            total_tokens = sum(u.get("total_tokens", 0) for u in self.usage_metadata.values())
        return TraceMetrics(
            n_turns=turns or None,
            n_toolcalls=toolcalls or None,
            n_total_tokens=total_tokens or None,
            n_prompt_tokens=prompt_tokens or None,
            n_completion_tokens=completion_tokens or None,
        )
