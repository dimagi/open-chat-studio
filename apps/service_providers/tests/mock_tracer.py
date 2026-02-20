from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing import Tracer
from apps.service_providers.tracing.base import TraceContext
from apps.service_providers.tracing.const import SpanLevel


class MockTracer(Tracer):
    def __init__(self):
        super().__init__("mock", {})
        # Don't set self.trace here to avoid shadowing the trace() method
        # Instead, we'll use _trace_data internally and add a property
        self._trace_data = None
        self.spans: dict[UUID, dict] = {}
        self.tags = None

    @property
    def ready(self) -> bool:
        return self._trace_data is not None

    # Expose trace data for tests (use _trace_data property)
    @property
    def trace_result(self):
        """Property to access trace data for test assertions."""
        return self._trace_data

    @contextmanager
    def trace(
        self,
        trace_context: TraceContext,
        session: ExperimentSession,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[TraceContext]:
        """Context manager for mock trace."""
        # Set base class state
        self.trace_name = trace_context.name
        self.trace_id = trace_context.id
        self.session = session

        # Create mock trace
        self._trace_data = {
            "name": trace_context.name,
            "id": trace_context.id,
            "session_id": session.id,
            "user_id": session.participant.identifier,
            "inputs": inputs or {},
            "metadata": metadata or {},
            "ended": False,
        }

        error_to_record: Exception | None = None

        try:
            yield trace_context
        except Exception as e:
            error_to_record = e
            raise
        finally:
            # Get outputs from the context object
            outputs = trace_context.outputs if trace_context.outputs else None

            # Mark as ended and store outputs/error
            self._trace_data["outputs"] = outputs
            self._trace_data["error"] = str(error_to_record) or str(trace_context.exception)
            self._trace_data["ended"] = True

            # Reset state
            self.trace_name = None
            self.trace_id = None
            self.session = None

    @contextmanager
    def span(
        self,
        span_context: TraceContext,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> Iterator[TraceContext]:
        """Context manager for mock span."""
        # Create mock span
        self.spans[span_context.id] = {
            "name": span_context.name,
            "inputs": inputs,
            "metadata": metadata or {},
            "level": level,
            "ended": False,
        }

        error_to_record: Exception | None = None

        try:
            yield span_context
        except Exception as e:
            error_to_record = e
            raise
        finally:
            # Get outputs from the context object
            outputs = span_context.outputs if span_context.outputs else {}

            # Mark as ended and store outputs/error
            span = self.spans[span_context.id]
            span["outputs"] = outputs
            span["error"] = str(error_to_record) or str(span_context.exception)
            span["ended"] = True

    def get_langchain_callback(self) -> BaseCallbackHandler:
        return MagicMock()

    def get_trace_metadata(self) -> dict[str, str]:
        return {"trace_id": str(self._trace_data["id"])}

    def add_trace_tags(self, tags: list[str]) -> None:
        self.tags = tags

    def set_output_message_id(self, message_id: int):  # ty: ignore[invalid-method-override]
        pass

    def set_input_message_id(self, message_id: int):  # ty: ignore[invalid-method-override]
        pass
