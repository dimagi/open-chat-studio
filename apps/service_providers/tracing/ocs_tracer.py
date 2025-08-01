from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

from apps.service_providers.tracing.const import OCS_TRACE_PROVIDER, SpanLevel
from apps.trace.models import Trace

from .base import Tracer

if TYPE_CHECKING:
    from apps.experiments.models import ExperimentSession

logger = logging.getLogger("ocs.tracing")


# TODO in followup PR: Return link to trace in get_trace_metadata
class OCSTracer(Tracer):
    """
    Internal OCS tracer that creates Trace objects in the database.
    """

    def __init__(self, experiment_id: int, team_id: int):
        super().__init__(OCS_TRACE_PROVIDER, {})
        self.experiment_id = experiment_id
        self.team_id = team_id
        self.output_message_id: str = None
        self.start_time: float = None

    @property
    def ready(self) -> bool:
        """OCS tracer is always ready when experiment_id and team_id are set."""
        return self.experiment_id and self.team_id

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

            Trace.objects.create(
                experiment_id=self.experiment_id,
                session_id=self.session.id,
                participant=self.session.participant,
                output_message_id=self.output_message_id,
                duration=duration_ms,
                team_id=self.team_id,
            )
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
                self.output_message_id,
            )
        finally:
            super().end_trace(outputs, error)

    def start_span(
        self,
        span_id: UUID,
        span_name: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> None:
        # OCS tracer doesn't track individual spans, only the overall trace.
        pass

    def end_span(
        self,
        span_id: UUID,
        outputs: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        # OCS tracer doesn't track individual spans, only the overall trace.
        pass

    def get_langchain_callback(self) -> None:
        """Return a mock callback handler since OCS tracer doesn't need LangChain integration."""
        return None

    def add_trace_tags(self, tags: list[str]) -> None:
        pass

    def set_output_message_id(self, output_message_id: str) -> None:
        """Set the output message ID for the trace."""
        self.output_message_id = output_message_id
