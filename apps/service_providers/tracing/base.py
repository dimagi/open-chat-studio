from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
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


@dataclasses.dataclass
class TraceContext:
    """Context object for active traces and spans.

    Holds state and outputs, yielded from trace/span context managers.
    This unified class is used for both trace-level and span-level contexts.
    """

    id: UUID
    name: str
    outputs: dict[str, Any] = dataclasses.field(default_factory=dict)

    def set_outputs(self, outputs: dict[str, Any]) -> None:
        """Set outputs for this trace/span. Can be called multiple times to merge outputs."""
        self.outputs |= outputs or {}


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
    @contextmanager
    def trace(
        self,
        trace_context: TraceContext,
        session: ExperimentSession,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[TraceContext]:
        """Context manager for trace lifecycle.

        Sets up tracing context on entry and ensures cleanup on exit.
        Yields the TraceContext object that can be used to set outputs.

        Args:
            trace_context: The context object with id, name, and outputs
            session: The experiment session for this trace
            inputs: Optional input data for the trace
            metadata: Optional metadata for the trace

        Yields:
            TraceContext: The same context object for setting outputs

        Example:
            ctx = TraceContext(id=trace_id, name=trace_name)
            with tracer.trace(ctx, session) as ctx:
                # tracing active
                ctx.set_outputs({"result": "value"})
            # cleanup guaranteed, outputs available in ctx.outputs
        """
        raise NotImplementedError

    @abstractmethod
    @contextmanager
    def span(
        self,
        span_context: TraceContext,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> Iterator[TraceContext]:
        """Context manager for span lifecycle.

        Sets up span context on entry and ensures cleanup on exit.
        Yields the TraceContext object that can be used to set outputs.

        Args:
            span_context: The context object with id, name, and outputs
            inputs: Input data for the span
            metadata: Optional metadata for the span
            level: Span level (DEFAULT, WARNING, ERROR)

        Yields:
            TraceContext: The same context object for setting outputs
        """
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
