from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

import sentry_sdk
from langchain_core.runnables import RunnableConfig

from .base import TraceContext, Tracer
from .callback import wrap_callback

if TYPE_CHECKING:
    from langchain.callbacks.base import BaseCallbackHandler

    from apps.experiments.models import ExperimentSession


logger = logging.getLogger("ocs.tracing")


class TracingService:
    def __init__(self, tracers: list[Tracer], experiment_id: int, team_id: int):
        self._tracers = tracers

        self.outputs: dict[UUID, dict] = defaultdict(dict)
        self.span_stack: list[TraceContext] = []

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
    def create_for_experiment(cls, experiment) -> Self:
        from apps.service_providers.tracing.ocs_tracer import OCSTracer

        tracers = []
        if experiment and experiment.id and experiment.team_id:
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
    ) -> Iterator[TraceContext]:
        """Context manager for tracing or spanning.

        This context manager will start a trace if there isn't already one,
        otherwise it will start a span.
        """
        if not self.trace_id:
            with self.trace(name, session, inputs, metadata) as ctx:
                yield ctx
        else:
            with self.span(name, inputs, metadata) as ctx:
                yield ctx

    @contextmanager
    def trace(
        self,
        trace_name: str,
        session: ExperimentSession,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Iterator[TraceContext]:
        """Context manager for tracing."""
        self.trace_id = uuid.uuid4()
        self.trace_name = trace_name
        self.session = session
        self._start_time = time.time()

        # Create context object for this trace
        trace_context = TraceContext(id=self.trace_id, name=trace_name)

        try:
            with ExitStack() as stack:
                # Enter all tracer contexts
                for tracer in self._tracers:
                    try:
                        stack.enter_context(
                            tracer.trace(
                                trace_context=trace_context,
                                session=session,
                                inputs=inputs,
                                metadata=metadata,
                            )
                        )
                    except Exception:
                        logger.exception("Error initializing tracer %s", tracer.__class__.__name__)

                sentry_sdk.set_context("Traces", self.get_trace_metadata())

                # Yield the context object to user code
                yield trace_context
        finally:
            self._reset()

    @contextmanager
    def span(
        self,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[TraceContext]:
        """Context manager for spanning."""
        # Create context object that will be passed to tracers and yielded to user
        span_id = uuid.uuid4()
        span_context = TraceContext(id=span_id, name=span_name)

        if not self.activated:
            # Return a dummy context if not activated
            yield span_context
            return

        self.span_stack.append(span_context)
        try:
            with ExitStack() as stack:
                # Enter all tracer span contexts, passing the same context object
                for tracer in self._active_tracers:
                    try:
                        stack.enter_context(
                            tracer.span(
                                span_context=span_context,
                                inputs=inputs,
                                metadata=metadata or {},
                            )
                        )
                    except Exception:
                        logger.exception(f"Error starting span {span_name} in tracer {tracer.__class__.__name__}")

                # Yield the context object to user code
                yield span_context
        finally:
            self.span_stack.pop()

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
        span_context = self._get_current_span_info()
        metadata = {}
        if self.session:
            metadata["participant-id"] = self.session.participant.identifier
            metadata["session-id"] = str(self.session.external_id)

        config = RunnableConfig(
            run_name=f"{span_context.name or 'OCS'} run",
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

    @property
    def _active_tracers(self) -> list[Tracer]:
        return [tracer for tracer in self._tracers if tracer.ready]

    def _reset(self) -> None:
        self.trace_id = None
        self.trace_name = None
        self.session = None
        self.outputs = defaultdict(dict)
        self.span_stack = []

    def _get_current_span_info(self) -> TraceContext:
        if self.span_stack:
            return self.span_stack[-1]
        return TraceContext(self.trace_id, self.trace_name)

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
