from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from apps.service_providers.tracing import Tracer
from apps.service_providers.tracing.const import SpanLevel


class MockTracer(Tracer):
    def __init__(self):
        super().__init__("mock", {})
        self.trace = None
        self.spans: dict[UUID, dict] = {}

    @property
    def ready(self) -> bool:
        return bool(self.trace)

    def _start_trace_internal(
        self,
        trace_name: str,
        trace_id: UUID,
        session_id: str,
        user_id: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.trace = {
            "name": trace_name,
            "id": trace_id,
            "session_id": session_id,
            "user_id": user_id,
            "inputs": inputs or {},
            "metadata": metadata or {},
        }

    def _end_trace_internal(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        if not self.trace:
            raise Exception("Trace has not been started.")
        self.trace["outputs"] = outputs
        self.trace["error"] = error
        self.trace["ended"] = True

    def _start_span_internal(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        self.spans[span_id] = {
            "name": span_name,
            "inputs": inputs,
            "metadata": metadata or {},
            "level": level,
            "attributes": {},
        }

    def _end_span_internal(
        self, span_id: UUID, outputs: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        span = self.spans[span_id]
        span["outputs"] = outputs or {}
        span["error"] = str(error) if error else None
        span["ended"] = True

    def _set_span_attribute(self, span_id: UUID, key: str, value: Any) -> None:
        """Set an attribute on a mock span."""
        if span_id in self.spans:
            self.spans[span_id]["attributes"][key] = value

    def _record_span_exception(self, span_id: UUID, exception: Exception) -> None:
        """Record an exception on a mock span."""
        if span_id in self.spans:
            self.spans[span_id]["exception"] = str(exception)

    # Backward compatibility methods
    def start_trace(
        self,
        trace_name: str,
        trace_id: UUID,
        session_id: str,
        user_id: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Deprecated: Use trace() context manager instead."""
        super().start_trace(trace_name=trace_name, trace_id=trace_id, session_id=session_id, user_id=user_id)
        self._start_trace_internal(trace_name, trace_id, session_id, user_id, inputs, metadata)

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """Deprecated: Use trace() context manager instead."""
        self._end_trace_internal(outputs, error)
        super().end_trace(outputs=outputs, error=error)

    def start_span(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        """Deprecated: Use span() context manager instead."""
        self._start_span_internal(span_id, span_name, inputs, metadata, level)

    def end_span(self, span_id: UUID, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """Deprecated: Use span() context manager instead."""
        self._end_span_internal(span_id, outputs, error)

    def get_langchain_callback(self) -> BaseCallbackHandler:
        return MagicMock()

    def get_trace_metadata(self) -> dict[str, str]:
        return {"trace_id": str(self.trace["id"])}
