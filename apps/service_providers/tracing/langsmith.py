from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import langsmith as ls
from langchain_core.tracers import LangChainTracer
from langchain_core.tracers.langchain import wait_for_all_tracers
from langsmith import RunTree

from . import Tracer
from .base import ServiceNotInitializedException, ServiceReentryException
from .const import SpanLevel

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler


class LangSmithTracer(Tracer):
    def __init__(self, type_, config: dict):
        super().__init__(type_, config)
        self.client = None
        self.spans: dict[UUID, tuple[ls.trace, RunTree]] = {}
        self.context = None

    @property
    def ready(self) -> bool:
        return bool(self.client)

    def _start_trace_internal(
        self,
        trace_name: str,
        trace_id: UUID,
        session_id: str,
        user_id: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from langsmith import Client

        if self.client:
            raise ServiceReentryException("Service does not support reentrant use.")

        self.client = Client(
            api_url=self.config["api_url"],
            api_key=self.config["api_key"],
        )

        project_name = self.config["project"]

        metadata = metadata or {}
        metadata.update(
            {
                "session_id": session_id,
                "user_id": user_id,
            }
        )

        self.context = ls.tracing_context(
            project_name=project_name, metadata=metadata, client=self.client, enabled=True
        )
        self.context.__enter__()

        self._start_trace(
            trace_id,
            name=trace_name,
            inputs=inputs,
            tags=[f"user:{user_id}", f"session:{session_id}"],
        )

    def _end_trace_internal(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        trace_id = self.trace_id
        if not self.ready:
            return

        self._end_trace(trace_id, outputs, error)
        self.context.__exit__(None, None, None)
        wait_for_all_tracers()

        self.context = None
        self.client = None
        self.spans.clear()

    def _start_span_internal(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        if not self.ready:
            return

        self._start_trace(span_id, name=span_name, inputs=inputs, metadata=metadata or {})

    def _end_span_internal(
        self, span_id: UUID, outputs: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        if not self.ready:
            return

        self._end_trace(span_id, outputs, error)

    def _set_span_attribute(self, span_id: UUID, key: str, value: Any) -> None:
        """Set an attribute on a LangSmith span."""
        if not self.ready:
            return

        trace_context = self.spans.get(span_id)
        if trace_context:
            _, run_tree = trace_context
            try:
                # LangSmith RunTree doesn't have direct attribute setting, but we can add to extra
                if hasattr(run_tree, "extra") and run_tree.extra is not None:
                    run_tree.extra[key] = value
                else:
                    run_tree.extra = {key: value}
            except Exception:
                # If setting fails, ignore silently
                pass

    def _record_span_exception(self, span_id: UUID, exception: Exception) -> None:
        """Record an exception on a LangSmith span."""
        if not self.ready:
            return

        trace_context = self.spans.get(span_id)
        if trace_context:
            _, run_tree = trace_context
            run_tree.error = str(exception)

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
        super().start_trace(trace_name, trace_id, session_id, user_id, inputs, metadata)
        self._start_trace_internal(trace_name, trace_id, session_id, user_id, inputs, metadata)

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """Deprecated: Use trace() context manager instead."""
        self._end_trace_internal(outputs, error)
        super().end_trace(outputs, error)

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
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")
        return LangChainTracer(client=self.client)

    def get_trace_metadata(self) -> dict[str, str]:
        if not self.ready or not self.spans:
            return {}
        _, run_tree = self.spans[self.trace_id]
        return {
            "trace_id": str(self.trace_id),
            "trace_url": run_tree.get_url(),
            "trace_provider": self.type,
        }

    def _start_trace(self, trace_id: UUID, **kwargs):
        trace = ls.trace(run_id=trace_id, run_type="chain", client=self.client, **kwargs)
        run_tree = trace.__enter__()
        self.spans[trace_id] = (trace, run_tree)

    def _end_trace(self, trace_id: UUID, outputs: dict[str, Any] | None, error: Exception | None):
        trace, run_tree = self.spans.pop(trace_id)
        run_tree.end(outputs=outputs or {}, error=str(error) if error else None)
        trace.__exit__()
