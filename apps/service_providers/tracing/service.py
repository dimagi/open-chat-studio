import logging
import uuid
from collections import defaultdict
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

from .base import Tracer
from .callback import wrap_callback

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler


logger = logging.getLogger("ocs.tracing")


class TracingService:
    def __init__(self, tracers: list[Tracer]):
        self._tracers = tracers
        self.activated = False

        self.inputs: dict[str, dict] = defaultdict(dict)
        self.inputs_metadata: dict[str, dict] = defaultdict(dict)
        self.outputs: dict[str, dict] = defaultdict(dict)
        self.outputs_metadata: dict[str, dict] = defaultdict(dict)

        self.trace_name: str | None = None
        self.trace_id: UUID | None = None
        self.session_id: str | None = None
        self.user_id: str | None = None

    @classmethod
    def create_for_experiment(cls, experiment) -> Self:
        tracers = []
        if experiment and experiment.trace_provider:
            try:
                tracers.append(experiment.trace_provider.get_service())
            except Exception as e:  # noqa: BLE001
                logger.error(f"Error setting up trace service: {e}")

        return TracingService(tracers)

    @contextmanager
    def trace(self, trace_name: str, session_id: str, user_id: str):
        self.session_id = session_id
        self.trace_name = trace_name
        self.user_id = user_id
        self.trace_id = uuid.uuid4()

        try:
            self._begin_traces()
            self.activated = True
            yield self
        finally:
            self._end_traces()

    def _begin_traces(self):
        for tracer in self._active_tracers:
            try:
                tracer.begin_trace(self.trace_name, self.trace_id, self.session_id, self.user_id)
            except Exception:  # noqa BLE001
                logger.error("Error initializing tracer %s", tracer.__class__.__name__, exc_info=True)

    def _end_traces(self):
        for tracer in self._active_tracers:
            try:
                tracer.end_trace()
            except Exception:  # noqa BLE001
                logger.error("Error ending tracer %s", tracer.__class__.__name__, exc_info=True)
        self._reset_io()

    @contextmanager
    def span(
        self,
        span_id: str,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ):
        if not self.activated:
            yield self
            return

        self._start_span(
            span_id,
            span_name,
            inputs,
            metadata,
        )
        try:
            yield self
        except Exception as e:
            self._end_span(span_id, span_name, e)
            raise
        else:
            self._end_span(span_id, span_name)

    def set_outputs(
        self,
        span_id: str,
        outputs: dict[str, Any],
        output_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.outputs[span_id] |= outputs or {}
        self.outputs_metadata[span_id] |= output_metadata or {}

    def get_langchain_callbacks(self) -> list["BaseCallbackHandler"]:
        if not self.activated:
            return []

        callbacks = []
        for tracer in self._active_tracers:
            callback = tracer.get_langchain_callback()
            if callback:
                callbacks.append(wrap_callback(callback))

        return callbacks

    def get_trace_metadata(self) -> list[dict[str, Any]]:
        if not self.activated:
            return []

        trace_info = []
        for tracer in self._active_tracers:
            try:
                info = tracer.get_trace_metadata()
                trace_info.append(info)
            except Exception:  # noqa BLE001
                logger.exception("Error getting trace info")
                continue

        return trace_info

    def _start_span(
        self,
        span_id: str,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.inputs[span_id] = inputs
        self.inputs_metadata[span_id] = metadata or {}
        if not self.activated:
            return

        for tracer in self._active_tracers:
            try:
                tracer.start_span(
                    span_id=span_id,
                    span_name=span_name,
                    inputs=inputs,
                    metadata=metadata or {},
                )
            except Exception:  # noqa BLE001
                logger.exception(f"Error starting span {span_name}")

    def _end_span(self, span_id: str, span_name: str, error: Exception | None = None) -> None:
        if not self.activated:
            return

        for tracer in self._active_tracers:
            try:
                tracer.end_span(
                    span_id=span_id,
                    outputs=self.outputs[span_id],
                    error=error,
                )
            except Exception:  # noqa BLE001
                logger.exception(f"Error ending span {span_name}")

    @property
    def _active_tracers(self) -> list[Tracer]:
        return [tracer for tracer in self._tracers if tracer.ready]

    def _reset_io(self) -> None:
        self.inputs = defaultdict(dict)
        self.inputs_metadata = defaultdict(dict)
        self.outputs = defaultdict(dict)
        self.outputs_metadata = defaultdict(dict)
