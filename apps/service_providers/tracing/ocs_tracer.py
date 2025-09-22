from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.core.cache import cache
from django.utils import timezone
from langchain_core.callbacks.base import BaseCallbackHandler

from apps.annotations.models import TagCategories
from apps.service_providers.tracing.const import OCS_TRACE_PROVIDER, SpanLevel
from apps.trace.error_parser import get_tags_from_error
from apps.trace.models import Span, Trace, TraceStatus

from .base import Tracer

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
        """OCS tracer is always ready when no trace is active."""
        return self.trace is not None

    def start_trace(
        self,
        trace_name: str,
        trace_id: UUID,
        session: ExperimentSession,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Start a trace and record the start time."""
        super().start_trace(trace_name, trace_id, session, inputs, metadata)
        self.trace = Trace.objects.create(
            trace_id=trace_id,
            experiment_id=self.experiment_id,
            team_id=self.team_id,
            session=session,
            duration=0,
            participant=session.participant,
        )

        self.start_time = time.time()
        self.session = session

    def end_trace(self, outputs: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        """End the trace and create a Trace object in the database."""
        if not self.ready or not self.start_time:
            super().end_trace(outputs, error)
            return

        try:
            end_time = time.time()
            duration = end_time - self.start_time
            duration_ms = int(duration * 1000)

            self.trace.duration = duration_ms
            if self.error_detected:
                self.trace.status = TraceStatus.ERROR
            else:
                self.trace.status = TraceStatus.SUCCESS
            self.trace.save()

            logger.debug(
                "Created trace in DB | experiment_id=%s, session_id=%s, duration=%sms",
                self.experiment_id,
                self.session.id,
                duration_ms,
            )
        except Exception:
            logger.exception(
                "Error saving trace in DB | experiment_id=%s, session_id=%s, output_message_id=%s",
                self.experiment_id,
                self.session.id,
                self.trace.output_message_id,
            )
        finally:
            self.trace = None
            self.spans = {}
            self.error_detected = False
            super().end_trace(outputs, error)

    def start_span(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        if not self.ready:
            return

        self.spans[span_id] = self._get_current_observation().span(
            span_id=span_id,
            span_name=span_name,
            inputs=inputs,
            metadata=metadata or {},
        )

    def end_span(
        self,
        span_id: UUID,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        if not self.ready:
            return

        span = self.spans.pop(span_id, None)
        if not span:
            return

        span.output = outputs or {}
        span.end_time = timezone.now()
        if error:
            self.error_detected = True
            span.status = TraceStatus.ERROR
            span.error = str(error)

            if self.error_span_id is None:
                # Only tag the span in which the error occured
                self.error_span_id = span_id
                tags = get_tags_from_error(error)
                for tag in tags:
                    span.create_and_add_tag(tag=tag, team=span.team, tag_category=TagCategories.ERROR)

            self._bust_caches()
        else:
            span.status = TraceStatus.SUCCESS
        span.save()

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
    LANGCHAIN_CHAINS_TO_IGNORE = ["start", "end"]

    def __init__(self, tracer: OCSTracer):
        super().__init__()
        self.tracer = tracer

    def on_llm_start(self, serialized, prompts, run_id, parent_run_id, tags, metadata, *args, **kwargs) -> None:
        self.tracer.start_span(
            span_id=run_id,
            span_name=kwargs.get("name", "Unknown span"),
            inputs={"prompts": prompts},
            metadata=metadata or {},
        )

    def on_llm_end(self, response, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer.end_span(
            span_id=run_id,
            outputs={"output": response},
        )

    def on_llm_error(self, error, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer.end_span(
            span_id=run_id,
            error=error,
        )

    def on_chain_start(self, serialized, inputs, run_id, parent_run_id, tags, metadata, *args, **kwargs) -> None:
        metadata = metadata or {}
        serialized = serialized or {}
        chain_name = kwargs.get("name", "Unknown span")
        if chain_name in OCSCallbackHandler.LANGCHAIN_CHAINS_TO_IGNORE:
            return

        self.tracer.start_span(
            span_id=run_id,
            span_name=chain_name,
            inputs=inputs,
            metadata=metadata or {},
        )

    def on_chain_end(self, outputs, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer.end_span(
            span_id=run_id,
            outputs=outputs,
        )

    def on_chain_error(self, error, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer.end_span(
            span_id=run_id,
            outputs={},
            error=error,
        )

    def on_tool_start(self, serialized, input_str, run_id, parent_run_id, tags, metadata, *args, **kwargs) -> None:
        self.tracer.start_span(
            span_id=run_id,
            span_name=kwargs.get("name", "Unknown span"),
            inputs={"input": input_str},
            metadata=metadata or {},
        )

    def on_tool_end(self, output, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer.end_span(
            span_id=run_id,
            outputs={"output": output},
        )

    def on_tool_error(self, error, run_id, parent_run_id, *args, **kwargs) -> None:
        self.tracer.end_span(
            span_id=run_id,
            error=error,
        )

    def on_chat_model_start(self, *args, **kwargs) -> Any:
        pass
