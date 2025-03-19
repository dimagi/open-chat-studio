from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler


EventLevel = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]


class TraceInfo(BaseModel):
    provider_type: str
    trace_id: str
    trace_url: str


class BaseTracer(ABC):
    trace_provider_type = None

    @abstractmethod
    def __init__(self, client_config: dict):
        raise NotImplementedError

    @property
    @abstractmethod
    def ready(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def initialize(self, trace_name: str, trace_id: UUID, session_id: str, user_id: str):
        raise NotImplementedError

    @abstractmethod
    def start_span(
        self,
        span_id: str,
        trace_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def end_span(
        self,
        span_id: str,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def event(
        self,
        name: str,
        message: str,
        level: EventLevel = "DEFAULT",
        metadata: dict[str, Any] | None = None,
    ):
        raise NotImplementedError

    @abstractmethod
    def end(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_langchain_callback(self) -> BaseCallbackHandler | None:
        raise NotImplementedError

    @abstractmethod
    def get_current_trace_info(self) -> TraceInfo | None:
        raise NotImplementedError
