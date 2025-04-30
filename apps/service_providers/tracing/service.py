import logging
import uuid
from collections import defaultdict
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

from langchain_core.runnables import RunnableConfig

from .base import Tracer
from .callback import wrap_callback

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler


logger = logging.getLogger("ocs.tracing")


class TracingService:
    def __init__(self, tracers: list[Tracer]):
        self._tracers = tracers

        self.outputs: dict[UUID, dict] = defaultdict(dict)
        self.span_stack: list[tuple[UUID, str]] = []

        self.trace_name: str | None = None
        self.trace_id: UUID | None = None
        self.session_id: str | None = None
        self.user_id: str | None = None

    @classmethod
    def empty(cls) -> Self:
        return cls([])

    @classmethod
    def create_for_experiment(cls, experiment) -> Self:
        tracers = []
        if experiment and experiment.trace_provider:
            try:
                tracers.append(experiment.trace_provider.get_service())
            except Exception as e:  # noqa: BLE001
                logger.error(f"Error setting up trace service: {e}")

        return cls(tracers)

    @property
    def activated(self):
        return bool(self.trace_id)

    @contextmanager
    def trace_or_span(
        self, name: str, session_id: str, user_id: str, inputs: dict[str, Any], metadata: dict[str, Any] | None = None
    ):
        """Context manager for tracing or spanning.

        This context manager will start a trace if there isn't already one,
        otherwise it will start a span.
        """
        if not self.trace_id:
            with self.trace(name, session_id, user_id, inputs, metadata):
                yield self
        else:
            with self.span(name, inputs, metadata):
                yield self

    @contextmanager
    def trace(
        self,
        trace_name: str,
        session_id: str,
        user_id: str,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, str] | None = None,
    ):
        self.trace_id = uuid.uuid4()
        self.trace_name = trace_name
        self.session_id = session_id
        self.user_id = user_id

        try:
            self._start_traces(inputs, metadata)
            yield self
        except Exception as e:
            self._end_traces(e)
            raise
        else:
            self._end_traces()

    def _start_traces(self, inputs: dict[str, Any] | None = None, metadata: dict[str, str] | None = None):
        for tracer in self._tracers:
            try:
                tracer.start_trace(self.trace_name, self.trace_id, self.session_id, self.user_id, inputs, metadata)
            except Exception:  # noqa BLE001
                logger.exception("Error initializing tracer %s", tracer.__class__.__name__)

    def _end_traces(self, error: Exception | None = None):
        for tracer in self._active_tracers:
            try:
                tracer.end_trace(self.outputs.get(self.trace_id), error)
            except Exception:  # noqa BLE001
                logger.exception("Error ending tracer %s", tracer.__class__.__name__)
        self._reset()

    @contextmanager
    def span(
        self,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ):
        if not self.activated:
            yield self
            return

        span_id = uuid.uuid4()
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

    def set_current_span_outputs(
        self,
        outputs: dict[str, Any],
    ) -> None:
        if not self.activated:
            return
        span_id, _ = self._get_current_span_info()
        self.outputs[span_id] |= outputs or {}

    def get_langchain_callbacks(self) -> list["BaseCallbackHandler"]:
        if not self.activated:
            return []

        callbacks = []
        for tracer in self._active_tracers:
            callback = tracer.get_langchain_callback()
            if callback:
                callbacks.append(wrap_callback(callback))

        return callbacks

    def get_langchain_config(self, *, callbacks: list = None, configurable: dict = None) -> RunnableConfig:
        extra_callbacks = callbacks or []
        tracer_callbacks = self.get_langchain_callbacks()
        _, span_name = self._get_current_span_info()
        metadata = {}
        if self.user_id:
            metadata["participant-id"] = self.user_id
        if self.session_id:
            metadata["session-id"] = self.session_id
        config = RunnableConfig(
            run_name=f"{span_name or 'OCS'} run",
            callbacks=tracer_callbacks + extra_callbacks,
            metadata=metadata,
        )
        if configurable is not None:
            config["configurable"] = {**configurable}
        return config

    def get_trace_metadata(self) -> dict[str, Any]:
        if not self.activated:
            return {}

        trace_info = []
        for tracer in self._active_tracers:
            try:
                info = tracer.get_trace_metadata()
                trace_info.append(info)
            except Exception:  # noqa BLE001
                logger.exception("Error getting trace info")
                continue

        return {"trace_info": trace_info} if trace_info else {}

    def _start_span(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.activated:
            return

        self.span_stack.append((span_id, span_name))

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

    def _end_span(self, span_id: UUID, span_name: str, error: Exception | None = None) -> None:
        if not self.activated:
            return

        popped_span_id, _ = self.span_stack.pop()
        if popped_span_id != span_id:
            logger.error("Span ID mismatch: expected %s, got %s", popped_span_id, span_id)

        for tracer in self._active_tracers:
            try:
                tracer.end_span(
                    span_id=span_id,
                    outputs=self.outputs.get(span_id, None),
                    error=error,
                )
            except Exception:  # noqa BLE001
                logger.exception(f"Error ending span {span_name}")

    @property
    def _active_tracers(self) -> list[Tracer]:
        return [tracer for tracer in self._tracers if tracer.ready]

    def _reset(self) -> None:
        self.trace_id = None
        self.trace_name = None
        self.session_id = None
        self.user_id = None
        self.outputs = defaultdict(dict)
        self.span_stack = []

    def _get_current_span_info(self) -> tuple[UUID, str]:
        if self.span_stack:
            return self.span_stack[-1]
        return self.trace_id, self.trace_name
