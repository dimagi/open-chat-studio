from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

logger = logging.getLogger("ocs.tracing")

if TYPE_CHECKING:
    from uuid import UUID

    from langchain.callbacks.base import BaseCallbackHandler

    from ..models import TraceProviderType
    from .base import BaseTracer
    from .schema import Log


def _get_langfuse_tracer():
    from .langfuse import LangFuseTracer

    return LangFuseTracer


class TracingService:
    def __init__(self, type_: TraceProviderType = None, config: dict = None):
        self.type = type_
        self.config = config
        self.deactivated = not self.config

        self.inputs: dict[str, dict] = defaultdict(dict)
        self.inputs_metadata: dict[str, dict] = defaultdict(dict)
        self.outputs: dict[str, dict] = defaultdict(dict)
        self.outputs_metadata: dict[str, dict] = defaultdict(dict)

        self.run_name: str | None = None
        self.run_id: UUID | None = None
        self._tracer: BaseTracer | None = None
        self._logs: dict[str, list[Log | dict[Any, Any]]] = defaultdict(list)
        self.session_id: str | None = None
        self.user_id: str | None = None

    def _reset_io(self) -> None:
        self.inputs = defaultdict(dict)
        self.inputs_metadata = defaultdict(dict)
        self.outputs = defaultdict(dict)
        self.outputs_metadata = defaultdict(dict)
        self.logs = defaultdict(list)

    def initialize(self, session_id: str, run_name: str, user_id: str) -> None:
        if self.deactivated:
            return

        self.session_id = session_id
        self.run_name = run_name
        self.user_id = user_id
        self.run_id = uuid.uuid4()

        try:
            init = {
                "langfuse": self._initialize_langfuse_tracer,
            }[self.type]
            init()
        except Exception:  # noqa: BLE001
            logger.error("Error initializing tracers", exc_info=True)

    def _initialize_langfuse_tracer(self) -> None:
        langfuse_tracer = _get_langfuse_tracer()
        self._tracer = langfuse_tracer(
            trace_name=self.run_name,
            trace_id=self.run_id,
            session_id=self.session_id,
            user_id=self.user_id,
            config=self.config,
        )

    def _start_traces(
        self,
        trace_id: str,
        trace_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.inputs[trace_id] = inputs
        self.inputs_metadata[trace_id] = metadata or {}
        if self._tracer and self._tracer.ready:
            try:
                self._tracer.add_trace(trace_id, trace_name, inputs, metadata)
            except Exception:  # noqa: BLE001
                logger.exception(f"Error starting trace {trace_name}")

    def _end_traces(self, trace_id: str, trace_name: str, error: Exception | None = None) -> None:
        if self._tracer and self._tracer.ready:
            try:
                self._tracer.end_trace(
                    trace_id=trace_id,
                    outputs=self.outputs[trace_id],
                    error=error,
                    logs=self._logs[trace_id],
                )
            except Exception:  # noqa: BLE001
                logger.exception(f"Error ending trace {trace_name}")
        self._reset_io()

    def end(self, outputs: dict, error: Exception | None = None) -> None:
        if self._tracer and self._tracer.ready:
            try:
                self._tracer.end(self.inputs, outputs=self.outputs, error=error, metadata=outputs)
            except Exception:  # noqa: BLE001
                logger.exception("Error ending all traces")
        self._reset_io()

    def add_log(self, trace_id: str, log: Log) -> None:
        self._logs[trace_id].append(log)

    @contextmanager
    def trace_context(
        self,
        trace_id: str,
        trace_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ):
        if self.deactivated:
            yield self
            return
        self._start_traces(
            trace_id,
            trace_name,
            inputs,
            metadata,
        )
        try:
            yield self
        except Exception as e:
            self._end_and_reset(trace_id, trace_name, e)
            raise
        else:
            self._end_and_reset(trace_id, trace_name)

    def _end_and_reset(self, trace_id: str, trace_name: str, error: Exception | None = None) -> None:
        self._end_traces(trace_id, trace_name, error)

    def set_outputs(
        self,
        trace_id: str,
        outputs: dict[str, Any],
        output_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.outputs[trace_id] |= outputs or {}
        self.outputs_metadata[trace_id] |= output_metadata or {}

    def get_langchain_callbacks(self) -> list[BaseCallbackHandler]:
        if self.deactivated:
            return []
        if self._tracer and self._tracer.ready:
            return [self._tracer.get_langchain_callback()]
        return []
