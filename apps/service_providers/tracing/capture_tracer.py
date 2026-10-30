"""A tracer that keeps a run's LLM and tool calls in memory for evaluators.

It writes nothing: no `Trace` rows and no usage. The spans are read from `CaptureTracer.spans`
once the trace has closed. See the ADR-0050 amendment.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langgraph.types import Command

from apps.cost_tracking.services.estimation import response_text
from apps.service_providers.tracing.callback import get_langchain_run_name, safe_serialize
from apps.service_providers.tracing.const import CAPTURE_TRACE_PROVIDER, SpanLevel

from .base import ServiceNotInitializedException, TraceContext, Tracer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from uuid import UUID

    from apps.experiments.models import ExperimentSession


@dataclass(frozen=True)
class CapturedSpan:
    type: Literal["llm", "tool"]
    name: str
    node_id: str | None
    input: Any
    output: Any = None
    error: str | None = None


@dataclass(frozen=True)
class _PendingSpan:
    type: Literal["llm", "tool"]
    name: str
    node_id: str | None
    input: Any
    sequence: int


def _error_text(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _tool_output(output: Any) -> Any:
    """A tool's result as plain data.

    A tool that updates graph state returns a `Command` whose messages carry its reply; the
    state change reaches evaluators through the participant data diff.
    """
    if isinstance(output, Command):
        update = output.update if isinstance(output.update, dict) else {}
        return "\n".join(message.text for message in update.get("messages", []))
    return safe_serialize(output.content if isinstance(output, BaseMessage) else output)


def _pipeline_node_id(metadata: dict[str, Any]) -> str | None:
    """The outermost graph node a call ran under.

    An agent inside a pipeline node reports its own inner node (`model`, `tools`) as
    `langgraph_node`; the pipeline node is the first segment of the checkpoint namespace
    (`<node id>:<task id>|model:<task id>`).
    """
    namespace = metadata.get("langgraph_checkpoint_ns")
    if namespace:
        return namespace.split("|", maxsplit=1)[0].split(":", maxsplit=1)[0]
    return metadata.get("langgraph_node")


class CaptureCallbackHandler(BaseCallbackHandler):
    """Records completed LLM and tool calls, listed in the order the calls started.

    For a model's parallel tool calls that is the order the model requested them, however
    they finish. Events arrive from LangGraph's thread pool, so the pending calls, the
    start counter and the span list are guarded by one lock.
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._sequence = 0
        self._pending: dict[UUID, _PendingSpan] = {}
        self._spans: list[tuple[int, CapturedSpan]] = []

    @property
    def spans(self) -> list[CapturedSpan]:
        with self._lock:
            return [span for _, span in sorted(self._spans, key=lambda entry: entry[0])]

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._start("llm", serialized, [{"type": m.type, "content": m.content} for m in messages[0]], run_id, kwargs)

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **kwargs: Any) -> None:
        self._start("llm", serialized, prompts, run_id, kwargs)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, output=response_text(response))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, error=_error_text(error))

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._start("tool", serialized, safe_serialize(inputs if inputs is not None else input_str), run_id, kwargs)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, output=_tool_output(output))

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, error=_error_text(error))

    def _start(
        self,
        span_type: Literal["llm", "tool"],
        serialized: dict[str, Any],
        span_input: Any,
        run_id: UUID,
        kwargs: dict[str, Any],
    ) -> None:
        name = get_langchain_run_name(serialized, **kwargs)
        node_id = _pipeline_node_id(kwargs.get("metadata") or {})
        with self._lock:
            self._pending[run_id] = _PendingSpan(
                type=span_type,
                name=name,
                node_id=node_id,
                input=span_input,
                sequence=self._sequence,
            )
            self._sequence += 1

    def _finish(self, run_id: UUID, output: Any = None, error: str | None = None) -> None:
        with self._lock:
            pending = self._pending.pop(run_id, None)
            if pending is None:
                return
            span = CapturedSpan(
                type=pending.type,
                name=pending.name,
                node_id=pending.node_id,
                input=pending.input,
                output=output,
                error=error,
            )
            self._spans.append((pending.sequence, span))


class CaptureTracer(Tracer):
    """Exposes the LLM and tool calls made inside a trace as `spans` once it closes."""

    def __init__(self) -> None:
        super().__init__(CAPTURE_TRACE_PROVIDER, {})
        self.spans: list[CapturedSpan] = []
        self._handler: CaptureCallbackHandler | None = None

    @property
    def ready(self) -> bool:
        return self._handler is not None

    @contextmanager
    def trace(
        self,
        trace_context: TraceContext,
        session: ExperimentSession | None,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[TraceContext]:
        """Capture calls for the duration of the trace, then keep them on `spans`."""
        handler = CaptureCallbackHandler()
        self._handler = handler
        try:
            yield trace_context
        finally:
            self.spans = handler.spans
            self._handler = None

    @contextmanager
    def span(
        self,
        span_context: TraceContext,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> Iterator[TraceContext]:
        """No-op: only LLM and tool calls are captured."""
        yield span_context

    def get_langchain_callback(self) -> BaseCallbackHandler:
        if self._handler is None:
            raise ServiceNotInitializedException("No active trace")
        return self._handler

    def add_trace_tags(self, tags: list[str]) -> None:
        pass

    def set_output_message_id(self, output_message_id: str) -> None:
        pass

    def set_input_message_id(self, input_message_id: str) -> None:
        pass

    def set_participant_data_diff(self, diff: list[tuple[str, str | list, Any]]) -> None:
        pass
