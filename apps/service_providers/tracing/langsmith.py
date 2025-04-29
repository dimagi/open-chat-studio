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

    def start_trace(
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

        super().start_trace(trace_name, trace_id, session_id, user_id, inputs)

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

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        trace_id = self.trace_id
        super().end_trace(outputs=outputs, error=error)
        if not self.ready:
            return

        self._end_trace(trace_id, outputs, error)
        self.context.__exit__(None, None, None)
        wait_for_all_tracers()

        self.context = None
        self.client = None
        self.spans.clear()

    def start_span(
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

    def end_span(self, span_id: UUID, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        if not self.ready:
            return

        self._end_trace(span_id, outputs, error)

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
