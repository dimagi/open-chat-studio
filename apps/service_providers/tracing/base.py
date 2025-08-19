from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from apps.service_providers.tracing.const import SpanLevel

if TYPE_CHECKING:
    from apps.experiments.models import ExperimentSession


class ServiceReentryException(Exception):
    pass


class ServiceNotInitializedException(Exception):
    pass


class Tracer(ABC):
    def __init__(self, type_, config: dict):
        self.type = type_
        self.config = config

        self.trace_id: UUID = None
        self.session: ExperimentSession = None
        self.trace_name: str = None

    @property
    @abstractmethod
    def ready(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def start_trace(
        self,
        trace_name: str,
        trace_id: UUID,
        session: ExperimentSession,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """This must be called before any tracing methods are called.

        Args:
            trace_name (str): The name of the trace.
            trace_id (UUID): The unique identifier for the trace.
            session (ExperimentSession): The session object.
            user_id (str): The user identifier.
            inputs (dict[str, Any] | None): The inputs to the trace.
            metadata (dict[str, Any] | None): Additional metadata for the trace.
        """
        self.trace_name = trace_name
        self.trace_id = trace_id
        self.session = session

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """This must be called after all tracing methods are called to finalize the trace."""
        self.trace_name = None
        self.trace_id = None
        self.session = None

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

    @abstractmethod
    def add_trace_tags(self, tags: list[str]) -> None:
        pass

    @abstractmethod
    def set_output_message_id(self, output_message_id: str) -> None:
        pass

    @abstractmethod
    def set_input_message_id(self, input_message_id: str) -> None:
        pass


@dataclasses.dataclass
class TraceInfo:
    name: str
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
