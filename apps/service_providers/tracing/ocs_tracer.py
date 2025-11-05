from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.core.cache import cache
from langchain_core.callbacks.base import BaseCallbackHandler

from apps.service_providers.tracing.const import OCS_TRACE_PROVIDER, SpanLevel
from apps.trace.models import Span, Trace, TraceStatus

from .base import TraceContext, Tracer

if TYPE_CHECKING:
    from apps.experiments.models import ExperimentSession

logger = logging.getLogger("ocs.tracing")


class OCSTracer(Tracer):
    """
    Internal OCS tracer that creates Trace objects in the database.
    """

    def __init__(self, experiment_id: int, team_id: int):
        super().__init__(OCS_TRACE_PROVIDER, {})
        self.experiment_id = experiment_id
        self.team_id = team_id
        self.start_time: float = None
        self.trace = None
        self.spans: dict[UUID, Span] = {}
        self.error_detected = False
        # error_span_id is used to track the span in which an error occurred
        self.error_span_id = None

    @property
    def ready(self) -> bool:
        """OCS tracer is always ready when a trace is active."""
        return self.trace is not None

    @contextmanager
    def trace(
        self,
        trace_context: TraceContext,
        session: ExperimentSession,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[TraceContext]:
        """Context manager for OCS trace lifecycle.

        Creates a database Trace record on entry and updates it with
        duration and status on exit.
        """
        from apps.experiments.models import Experiment

        # Set base class state from context
        self.trace_name = trace_context.name
        self.trace_id = trace_context.id
        self.session = session

        # Determine experiment ID (handle versioning)
        try:
            experiment = Experiment.objects.get(id=self.experiment_id)
        except Experiment.DoesNotExist:
            logger.exception(f"Experiment with id {self.experiment_id} does not exist. Cannot start trace.")
            yield trace_context
            return

        experiment_id = self.experiment_id
        experiment_version_number = None
        if experiment.is_a_version:
            # Trace needs to be associated with the working version of the experiment
            experiment_id = experiment.working_version_id
            experiment_version_number = experiment.version_number

        # Create database trace record
        self.trace = Trace.objects.create(
            trace_id=trace_context.id,
            experiment_id=experiment_id,
            experiment_version_number=experiment_version_number,
            team_id=self.team_id,
            session=session,
            duration=0,
            participant=session.participant,
            participant_data=session.participant.get_data_for_experiment(session.experiment),
            session_state=session.state,
        )

        self.start_time = time.time()

        try:
            yield trace_context
        finally:
            # Guaranteed cleanup - update trace duration and status
            if self.trace and self.start_time:
                try:
                    end_time = time.time()
                    duration = end_time - self.start_time
                    duration_ms = int(duration * 1000)

                    self.trace.duration = duration_ms
                    if self.error_detected:
                        self.trace.status = TraceStatus.ERROR
                    else:
                        self.trace.status = TraceStatus.SUCCESS

                    # Note: OCSTracer doesn't store trace outputs in database
                    # but could access them via trace_context.outputs if needed

                    self.trace.save()

                    logger.debug(
                        "Created trace in DB | experiment_id=%s, session_id=%s, duration=%sms",
                        self.experiment_id,
                        session.id,
                        duration_ms,
                    )
                except Exception:
                    logger.exception(
                        "Error saving trace in DB | experiment_id=%s, session_id=%s, output_message_id=%s",
                        self.experiment_id,
                        session.id,
                        self.trace.output_message_id,
                    )

            # Reset state
            self.trace = None
            self.spans = {}
            self.error_detected = False
            self.trace_name = None
            self.trace_id = None
            self.session = None

    @contextmanager
    def span(
        self,
        span_context: TraceContext,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> Iterator[TraceContext]:
        """Context manager for OCS span lifecycle.

        Note: Span tracking is currently disabled due to multithreading
        reliability issues. This is a no-op context manager that yields
        immediately but still processes errors.
        """
        error_to_record: Exception | None = None

        try:
            yield span_context
        except Exception as e:
            error_to_record = e
            raise
        finally:
            if error_to_record:
                self.error_detected = True

                # Note: Span creation is disabled, but we still track errors
                # If span tracking is re-enabled, this is where we would:
                # 1. Get outputs from span_context.outputs
                # 2. Create and save span to database with outputs
                # 3. Add error tags if needed

                # Example if re-enabled:
                # if self.spans and span_context.id in self.spans:
                #     span = self.spans[span_context.id]
                #     span.output = span_context.outputs
                #     span.error = str(error_to_record)
                #     span.save()

    def _start_span_for_callback(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Internal method for LangChain callback handler.

        Span tracking is disabled, so this is a no-op.
        """
        pass

    def _end_span_for_callback(
        self,
        span_id: UUID,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Internal method for LangChain callback handler.

        Tracks errors even though span creation is disabled.
        """
        if error:
            self.error_detected = True

    def get_langchain_callback(self) -> None:
        """Return a mock callback handler since OCS tracer doesn't need LangChain integration."""
        return OCSCallbackHandler(tracer=self)

    def add_trace_tags(self, tags: list[str]) -> None:
        pass

    def set_output_message_id(self, output_message_id: str) -> None:
        """Set the output message ID for the trace."""
        if self.trace:
            self.trace.output_message_id = output_message_id

    def set_input_message_id(self, input_message_id: str) -> None:
        """Set the input message ID for the trace."""
        if self.trace:
            self.trace.input_message_id = input_message_id

    def _get_current_observation(self) -> Span | Trace:
        """
        Returns the most recent active span if one exists, otherwise returns the root trace.
        This ensures new spans are properly nested under their parent spans.
        """
        if self.spans:
            last_span = next(reversed(self.spans))
            return self.spans[last_span]
        else:
            return self.trace

    def get_trace_metadata(self) -> dict[str, Any]:
        if not self.ready:
            return

        return {
            "trace_id": self.trace.id,
            "trace_url": self.trace.get_absolute_url(),
            "trace_provider": self.type,
        }

    def _bust_caches(self):
        """
        Bust any relevant caches when an error is detected in a span.
        """
        from apps.experiments.models import Experiment

        cache_key = Experiment.TREND_CACHE_KEY_TEMPLATE.format(experiment_id=self.experiment_id)
        cache.delete(cache_key)


class OCSCallbackHandler(BaseCallbackHandler):
    LANGCHAIN_CHAINS_TO_IGNORE = [
        "start",
        "end",
        "should_continue",
        "RunnableSequence",
        "LangGraph",
        "Run Pipeline run",
    ]

    def __init__(self, tracer: OCSTracer):
        super().__init__()
        self.tracer = tracer

    def on_llm_start(self, serialized, prompts, run_id, parent_run_id, tags, metadata, *args, **kwargs) -> None:
        self.tracer._start_span_for_callback(
            span_id=run_id,
            span_name=kwargs.get("name", "Unknown span"),
            inputs={"prompts": prompts},
            metadata=metadata or {},
        )

    def on_llm_end(self, response, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer._end_span_for_callback(
            span_id=run_id,
            outputs={"output": response},
        )

    def on_llm_error(self, error, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer._end_span_for_callback(
            span_id=run_id,
            error=error,
        )

    def on_chain_start(self, serialized, inputs, run_id, parent_run_id, tags, metadata, *args, **kwargs) -> None:
        metadata = metadata or {}
        serialized = serialized or {}
        chain_name = kwargs.get("name", "Unknown span")
        if chain_name in OCSCallbackHandler.LANGCHAIN_CHAINS_TO_IGNORE:
            return

        self.tracer._start_span_for_callback(
            span_id=run_id,
            span_name=chain_name,
            inputs=inputs,
            metadata=metadata or {},
        )

    def on_chain_end(self, outputs, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer._end_span_for_callback(
            span_id=run_id,
            outputs=outputs,
        )

    def on_chain_error(self, error, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer._end_span_for_callback(
            span_id=run_id,
            outputs={},
            error=error,
        )

    def on_tool_start(self, serialized, input_str, run_id, parent_run_id, tags, metadata, *args, **kwargs) -> None:
        self.tracer._start_span_for_callback(
            span_id=run_id,
            span_name=kwargs.get("name", "Unknown span"),
            inputs={"input": input_str},
            metadata=metadata or {},
        )

    def on_tool_end(self, output, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer._end_span_for_callback(
            span_id=run_id,
            outputs={"output": output},
        )

    def on_tool_error(self, error, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer._end_span_for_callback(
            span_id=run_id,
            error=error,
        )

    def on_chat_model_start(self, *args, **kwargs) -> Any:
        pass
