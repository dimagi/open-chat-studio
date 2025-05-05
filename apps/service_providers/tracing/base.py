import dataclasses
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from apps.service_providers.tracing.const import SpanLevel


class ServiceReentryException(Exception):
    pass


class ServiceNotInitializedException(Exception):
    pass


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

    @abstractmethod
    def start_trace(
        self,
        trace_name: str,
        trace_id: UUID,
        session_id: str,
        user_id: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """This must be called before any tracing methods are called.

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

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """This must be called after all tracing methods are called to finalize the trace."""
        self.trace_name = None
        self.trace_id = None
        self.session_id = None
        self.user_id = None

    @abstractmethod
    def start_span(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def end_span(
        self,
        span_id: UUID,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_langchain_callback(self) -> BaseCallbackHandler:
        raise NotImplementedError

    def get_trace_metadata(self) -> dict[str, str]:
        return {}


@dataclasses.dataclass
class TraceInfo:
    name: str
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
