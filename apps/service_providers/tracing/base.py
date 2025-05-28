import dataclasses
from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from apps.service_providers.tracing.const import SpanLevel


class ServiceReentryException(Exception):
    pass


class ServiceNotInitializedException(Exception):
    pass


class TraceContext:
    """Context object for a trace span that provides OpenTelemetry-style interface."""

    def __init__(self, tracer: "Tracer", span_id: UUID, span_name: str):
        self.tracer = tracer
        self.span_id = span_id
        self.span_name = span_name
        self._ended = False

    def set_attribute(self, key: str, value: Any) -> None:
        """Set an attribute on the span."""
        if not self._ended:
            self.tracer._set_span_attribute(self.span_id, key, value)

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        """Set multiple attributes on the span."""
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def record_exception(self, exception: Exception) -> None:
        """Record an exception on the span."""
        if not self._ended:
            self.tracer._record_span_exception(self.span_id, exception)

    def _end(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """Internal method to end the span."""
        if not self._ended:
            self._ended = True
            self.tracer._end_span_internal(self.span_id, outputs, error)


class Tracer(ABC):
    def __init__(self, type_, config: dict):
        self.type = type_
        self.config = config

        self.trace_id = None
        self.session_id = None
        self.user_id = None
        self.trace_name = None

    @property
    @abstractmethod
    def ready(self) -> bool:
        raise NotImplementedError

    @contextmanager
    def trace(
        self,
        trace_name: str,
        trace_id: UUID,
        session_id: str,
        user_id: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Generator[None, None, None]:
        """Context manager for creating a trace.

        Args:
            trace_name (str): The name of the trace.
            trace_id (UUID): The unique identifier for the trace.
            session_id (str): The session identifier.
            user_id (str): The user identifier.
            inputs (dict[str, Any] | None): The inputs to the trace.
            metadata (dict[str, Any] | None): Additional metadata for the trace.
        """
        self.trace_name = trace_name
        self.trace_id = trace_id
        self.session_id = session_id
        self.user_id = user_id

        try:
            self._start_trace_internal(trace_name, trace_id, session_id, user_id, inputs, metadata)
            yield
        except Exception as e:
            self._end_trace_internal(None, e)
            raise
        else:
            self._end_trace_internal()
        finally:
            self.trace_name = None
            self.trace_id = None
            self.session_id = None
            self.user_id = None

    @contextmanager
    def span(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> Generator[TraceContext, None, None]:
        """Context manager for creating a span.

        Args:
            span_id (UUID): The unique identifier for the span.
            span_name (str): The name of the span.
            inputs (dict[str, Any]): The inputs to the span.
            metadata (dict[str, Any] | None): Additional metadata for the span.
            level (SpanLevel): The level of the span.
        """
        context = TraceContext(self, span_id, span_name)
        try:
            self._start_span_internal(span_id, span_name, inputs, metadata, level)
            yield context
        except Exception as e:
            context._end(None, e)
            raise
        else:
            context._end()

    # Abstract methods that implementations must provide
    @abstractmethod
    def _start_trace_internal(
        self,
        trace_name: str,
        trace_id: UUID,
        session_id: str,
        user_id: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Internal method to start a trace."""
        raise NotImplementedError

    @abstractmethod
    def _end_trace_internal(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """Internal method to end a trace."""
        raise NotImplementedError

    @abstractmethod
    def _start_span_internal(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        """Internal method to start a span."""
        raise NotImplementedError

    @abstractmethod
    def _end_span_internal(
        self,
        span_id: UUID,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Internal method to end a span."""
        raise NotImplementedError

    @abstractmethod
    def _set_span_attribute(self, span_id: UUID, key: str, value: Any) -> None:
        """Set an attribute on a span. Override in implementations that support it."""
        pass

    @abstractmethod
    def _record_span_exception(self, span_id: UUID, exception: Exception) -> None:
        """Record an exception on a span. Override in implementations that support it."""
        pass

    @abstractmethod
    def get_langchain_callback(self) -> BaseCallbackHandler:
        raise NotImplementedError

    def get_trace_metadata(self) -> dict[str, str]:
        return {}

    # Backward compatibility methods (deprecated)
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
        self.trace_name = trace_name
        self.trace_id = trace_id
        self.session_id = session_id
        self.user_id = user_id
        self._start_trace_internal(trace_name, trace_id, session_id, user_id, inputs, metadata)

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """Deprecated: Use trace() context manager instead."""
        self._end_trace_internal(outputs, error)
        self.trace_name = None
        self.trace_id = None
        self.session_id = None
        self.user_id = None

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

    def end_span(
        self,
        span_id: UUID,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Deprecated: Use span() context manager instead."""
        self._end_span_internal(span_id, outputs, error)


@dataclasses.dataclass
class TraceInfo:
    name: str
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
