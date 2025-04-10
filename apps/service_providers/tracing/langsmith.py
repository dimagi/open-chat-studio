from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from langchain_core.tracers import LangChainTracer

from . import Tracer
from .base import ServiceReentryException

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler


class LangSmithTracer(Tracer):
    def __init__(self, type_, config: dict):
        super().__init__(type_, config)
        self.client = None
        self.session_id = None
        self.user_id = None

    @property
    def ready(self) -> bool:
        return bool(self.client)

    def begin_trace(self, trace_name: str, trace_id: UUID, session_id: str, user_id: str):
        from langsmith import Client

        if self.client:
            raise ServiceReentryException("Service does not support reentrant use.")

        super().begin_trace(trace_name, trace_id, session_id, user_id)

        self.client = Client(
            api_url=self.config["api_url"],
            api_key=self.config["api_key"],
        )

    def start_span(
        self, span_id: str, span_name: str, inputs: dict[str, Any], metadata: dict[str, Any] | None = None
    ) -> None:
        pass

    def end_span(self, span_id: str, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        pass

    def get_langchain_callback(self) -> BaseCallbackHandler:
        return LangChainTracer(client=self.client, project_name=self.config["project"])
