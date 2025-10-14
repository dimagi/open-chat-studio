from __future__ import annotations

import logging
import time
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

    from apps.experiments.models import ExperimentSession


logger = logging.getLogger("ocs.tracing")


class TracingService:
    def __init__(self, tracers: list[Tracer], experiment_id: int, team_id: int):
        self._tracers = tracers

        self.outputs: dict[UUID, dict] = defaultdict(dict)
        self.span_stack: list[tuple[UUID, str]] = []

        self.trace_name: str | None = None
        self.trace_id: UUID | None = None
        self.session: ExperimentSession | None = None
        self.experiment_id: int | None = experiment_id
        self.start_time = None
        self._input_message_id = None
        self._output_message_id = None
        self.team_id: int | None = team_id

        if (self.experiment_id is None or self.team_id is None) and self._tracers:
            raise ValueError("Tracers must be empty if experiment_id or team_id is None")

    @classmethod
    def empty(cls) -> Self:
        return cls([], None, None)

    @classmethod
    def create_for_experiment(cls, experiment, include_ocs_tracer=True) -> Self:
        from apps.service_providers.tracing.ocs_tracer import OCSTracer

        tracers = []
        if include_ocs_tracer and experiment and experiment.id and experiment.team_id:
            ocs_tracer = OCSTracer(experiment.id, experiment.team_id)
            tracers.append(ocs_tracer)

        if experiment and experiment.trace_provider:
            try:
                tracers.append(experiment.trace_provider.get_service())
            except Exception as e:  # noqa: BLE001
                logger.error(f"Error setting up trace service: {e}")

        return cls(tracers, experiment_id=experiment.id, team_id=experiment.team_id)

    @property
    def activated(self):
        return bool(self.trace_id)

    @contextmanager
    def trace_or_span(
        self,
        name: str,
        session: ExperimentSession,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        input_message_id: int | None = None,
    ):
        """Context manager for tracing or spanning.

        This context manager will start a trace if there isn't already one,
        otherwise it will start a span.
        """
        if not self.trace_id:
            with self.trace(name, session, inputs, metadata):
                yield self
        else:
            with self.span(name, inputs, metadata):
                yield self

    @contextmanager
    def trace(
        self,
        trace_name: str,
        session: ExperimentSession,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, str] | None = None,
    ):
        self.trace_id = uuid.uuid4()
        self.trace_name = trace_name
        self.session = session
        self._start_time = time.time()

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
                tracer.start_trace(
                    trace_name=self.trace_name,
                    trace_id=self.trace_id,
                    session=self.session,
                    inputs=inputs,
                    metadata=metadata,
                )
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

    def get_langchain_callbacks(
        self, run_name_map: dict[str, str] = None, filter_patterns: list[str] = None
    ) -> list[BaseCallbackHandler]:
        if not self.activated:
            return []

        callbacks = []
        for tracer in self._active_tracers:
            callback = tracer.get_langchain_callback()
            if callback:
                callbacks.append(wrap_callback(callback, run_name_map, filter_patterns))

        return callbacks

    def get_langchain_config(
        self,
        *,
        callbacks: list = None,
        configurable: dict = None,
        run_name_map: dict[str, str] = None,
        filter_patterns: list[str] = None,
    ) -> RunnableConfig:
        """
        Generates a RunnableConfig object with specific attributes and callbacks.

        Args:
            callbacks (list): Additional callbacks to be included in the runnable
                configuration. Defaults to None.
            configurable (dict): Key-value pairs to include as a configurable metadata
                in the configuration. Defaults to None.
            run_name_map (dict[str, str]): A map of run names for specific contexts.
                Used internally to map run names to more usable values.
            filter_patterns (list[str]): A list of patterns to filter spans by name.

        Returns:
            RunnableConfig: A configuration object with the combined callbacks,
            metadata, and run-specific details.
        """
        extra_callbacks = callbacks or []
        tracer_callbacks = self.get_langchain_callbacks(run_name_map, filter_patterns)
        _, span_name = self._get_current_span_info()
        metadata = {}
        if self.session:
            metadata["participant-id"] = self.session.participant.identifier
            metadata["session-id"] = str(self.session.external_id)

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
                if info := tracer.get_trace_metadata():
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
        self.session = None
        self.outputs = defaultdict(dict)
        self.span_stack = []

    def _get_current_span_info(self) -> tuple[UUID, str]:
        if self.span_stack:
            return self.span_stack[-1]
        return self.trace_id, self.trace_name

    def add_output_message_tags_to_trace(self, tags: list[str]) -> None:
        if not self.activated or not tags:
            return
        for tracer in self._active_tracers:
            try:
                tracer.add_trace_tags(tags)
            except Exception:
                logger.exception(f"Tracer {tracer.__class__.__name__} failed to add tags.")

    def set_output_message_id(self, output_message_id: str) -> None:
        for tracer in self._active_tracers:
            tracer.set_output_message_id(output_message_id)

    def set_input_message_id(self, input_message_id: str) -> None:
        for tracer in self._active_tracers:
            tracer.set_input_message_id(input_message_id)
