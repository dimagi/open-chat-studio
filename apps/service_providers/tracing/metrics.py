from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult


@dataclass
class TraceMetrics:
    """Metrics collected during a single pipeline execution."""

    n_turns: int | None = None
    n_toolcalls: int | None = None
    n_total_tokens: int | None = None
    n_prompt_tokens: int | None = None
    n_completion_tokens: int | None = None


class MetricsCollector(BaseCallbackHandler):
    """Thread-safe accumulator for pipeline execution metrics.

    Registers as a LangChain callback handler to collect turn counts,
    tool call counts, and token usage across all LLM calls
    in a pipeline execution.

    All counter access is protected by a threading lock because
    LangGraph executes nodes in threads via DjangoSafeContextThreadPoolExecutor.
    """

    def __init__(self, start_time: float):
        super().__init__()
        self._lock = threading.Lock()
        self._turns = 0
        self._toolcalls = 0
        self._total_tokens = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        with self._lock:
            self._turns += 1

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        token_usage = {}
        if response.llm_output:
            token_usage = response.llm_output.get("token_usage", {})

        prompt_tokens = token_usage.get("prompt_tokens", 0) or 0
        completion_tokens = token_usage.get("completion_tokens", 0) or 0

        with self._lock:
            self._total_tokens += prompt_tokens + completion_tokens
            self._prompt_tokens += prompt_tokens
            self._completion_tokens += completion_tokens

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        with self._lock:
            self._toolcalls += 1

    def get_metrics(self) -> TraceMetrics:
        """Return collected metrics, converting zero counts to None.

        Zero counts are converted to None to distinguish "no LLM calls happened"
        from "LLM calls happened but produced 0 tokens".
        """
        with self._lock:
            return TraceMetrics(
                n_turns=self._turns or None,
                n_toolcalls=self._toolcalls or None,
                n_total_tokens=self._total_tokens or None,
                n_prompt_tokens=self._prompt_tokens or None,
                n_completion_tokens=self._completion_tokens or None,
            )
