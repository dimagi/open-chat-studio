from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing import Tracer
from apps.service_providers.tracing.const import SpanLevel


class MockTracer(Tracer):
    def __init__(self):
        super().__init__("mock", {})
        self.trace = None
        self.spans: dict[UUID, dict] = {}
        self.tags = None

    @property
    def ready(self) -> bool:
        return bool(self.trace)

    def start_trace(
        self,
        trace_name: str,
        trace_id: UUID,
        session: ExperimentSession,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().start_trace(trace_name=trace_name, trace_id=trace_id, session=session)
        self.trace = {
            "name": trace_name,
            "id": trace_id,
            "session_id": session.id,
            "user_id": session.participant.identifier,
            "inputs": inputs or {},
            "metadata": metadata or {},
        }

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        super().end_trace(outputs=outputs, error=error)

        if not self.trace:
            raise Exception("Trace has not been started.")
        self.trace["outputs"] = outputs
        self.trace["error"] = error
        self.trace["ended"] = True

    def start_span(
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
        }

    def end_span(self, span_id: UUID, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        span = self.spans[span_id]
        span["outputs"] = outputs or {}
        span["error"] = str(error) if error else None
        span["ended"] = True

    def get_langchain_callback(self) -> BaseCallbackHandler:
        return MagicMock()

    def get_trace_metadata(self) -> dict[str, str]:
        return {"trace_id": str(self.trace["id"])}

    def add_trace_tags(self, tags: list[str]) -> None:
        self.tags = tags

    def set_output_message_id(self, message_id: int):
        pass

    def set_input_message_id(self, message_id: int):
        pass
