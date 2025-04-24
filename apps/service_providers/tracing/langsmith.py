from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from langchain_core.tracers import LangChainTracer
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
        self.root_run_tree = None
        self.spans: dict[UUID, RunTree] = {}

    @property
    def ready(self) -> bool:
        return bool(self.client)

    def start_trace(self, trace_name: str, trace_id: UUID, session_id: str, user_id: str):
        from langsmith import Client

        if self.client:
            raise ServiceReentryException("Service does not support reentrant use.")

        super().start_trace(trace_name, trace_id, session_id, user_id)

        self.client = Client(
            api_url=self.config["api_url"],
            api_key=self.config["api_key"],
        )

        project_name = self.config["project"]

        metadata = {
            "session_id": session_id,
            "user_id": user_id,
        }

        self.root_run_tree = RunTree(
            id=trace_id,
            name=trace_name,
            run_type="chain",
            ls_client=self.client,
            project_name=project_name,
            extra={"metadata": metadata},
            tags=[f"user:{user_id}", f"session:{session_id}"],
        )
        self.root_run_tree.post()

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        super().end_trace(outputs=outputs, error=error)
        if not self.ready or not self.root_run_tree:
            return

        self.root_run_tree.end(outputs=outputs or {}, error=str(error) if error else None)
        self.root_run_tree.patch()

        self.client = None
        self.root_run_tree = None
        self.spans.clear()

    def start_span(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        if not self.ready or not self.root_run_tree:
            return

        span_tree = self._get_parent_run_tree().create_child(
            run_id=span_id,
            name=span_name,
            run_type="chain",
            inputs=inputs,
            extra={"metadata": metadata or {}},
        )
        span_tree.post()

        # Store the span for later reference
        self.spans[span_id] = span_tree

    def end_span(self, span_id: UUID, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        if not self.ready or not self.root_run_tree:
            return

        span_tree = self.spans.pop(span_id, None)
        if span_tree:
            span_tree.end(outputs=outputs or {}, error=str(error) if error else None)
            span_tree.patch()

    def get_langchain_callback(self) -> BaseCallbackHandler:
        if not self.ready:
            raise ServiceNotInitializedException("Service not initialized.")
        return LangChainTracer(client=self.client, project_name=self.config["project"])

    def _get_parent_run_tree(self) -> RunTree:
        if self.spans:
            last_span = next(reversed(self.spans))
            return self.spans[last_span]
        else:
            return self.root_run_tree
