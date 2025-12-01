from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from langchain_core.callbacks.base import BaseCallbackHandler

from apps.service_providers.tracing.const import OCS_TRACE_PROVIDER, SpanLevel
from apps.trace.models import Trace, TraceStatus

from .base import TraceContext, Tracer

if TYPE_CHECKING:
    from apps.experiments.models import ExperimentSession

logger = logging.getLogger("ocs.tracing")


class OCSTracer(Tracer):
    """
    Internal OCS tracer that creates Trace objects in the database.
    """

    def __init__(self, experiment):
        super().__init__(OCS_TRACE_PROVIDER, {})
        self.experiment = experiment
        self.experiment_id = experiment.id
        self.team_id = experiment.team_id
        self.team_slug = experiment.team.slug
        self.start_time: float = None
        self.trace_record = None
        self.error_detected = False

    @property
    def ready(self) -> bool:
        """OCS tracer is always ready when a trace is active."""
        return self.trace_record is not None

    @staticmethod
    def _is_async_context() -> bool:
        """Check if we're currently in an async context."""
        try:
            asyncio.current_task()
            return True
        except RuntimeError:
            return False

    def _get_experiment_sync(self, experiment_id: int):
        """Sync version of getting experiment."""
        from apps.experiments.models import Experiment

        return Experiment.objects.get(id=experiment_id)

    def _create_trace(self, trace_context, session, experiment_id, experiment_version_number):
        """Sync version of creating trace."""
        return Trace.objects.acreate(
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

    async def _acreate_trace(self, trace_context, session, experiment_id, experiment_version_number):
        """Sync version of creating trace."""
        participant_data = await session.participant.aget_data_for_experiment(session.experiment)
        return await Trace.objects.acreate(
            trace_id=trace_context.id,
            experiment_id=experiment_id,
            experiment_version_number=experiment_version_number,
            team_id=self.team_id,
            session=session,
            duration=0,
            participant=session.participant,
            participant_data=participant_data,
            session_state=session.state,
        )

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

        # Set base class state from context
        self.trace_name = trace_context.name
        self.trace_id = trace_context.id
        self.session = session

        experiment_id = self.experiment_id
        experiment_version_number = None
        if self.experiment.is_a_version:
            # Trace needs to be associated with the working version of the experiment
            experiment_id = self.experiment.working_version_id
            experiment_version_number = self.experiment.version_number

        # Create database trace record
        self.trace_record = self._create_trace_sync(trace_context, session, experiment_id, experiment_version_number)

        self.start_time = time.time()

        try:
            yield trace_context
        except Exception:
            self.error_detected = True
            raise
        finally:
            # Guaranteed cleanup - update trace duration and status
            if self.trace_record and self.start_time:
                try:
                    end_time = time.time()
                    duration = end_time - self.start_time
                    duration_ms = int(duration * 1000)

                    self.trace_record.duration = duration_ms
                    if self.error_detected:
                        self.trace_record.status = TraceStatus.ERROR
                    else:
                        self.trace_record.status = TraceStatus.SUCCESS

                    # Note: OCSTracer doesn't store trace outputs in database
                    # but could access them via trace_context.outputs if needed
                    self.trace_record.save()
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
                        self.trace_record.output_message_id,
                    )

            # Reset state
            self.trace_record = None
            self.error_detected = False
            self.trace_name = None
            self.trace_id = None
            self.session = None

    @asynccontextmanager
    async def atrace(
        self,
        trace_context: TraceContext,
        session: ExperimentSession,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[TraceContext]:
        """Context manager for OCS trace lifecycle.

        Creates a database Trace record on entry and updates it with
        duration and status on exit.
        """
        # Set base class state from context
        self.trace_name = trace_context.name
        self.trace_id = trace_context.id
        self.session = session

        experiment_id = self.experiment_id
        experiment_version_number = None
        if self.experiment.is_a_version:
            # Trace needs to be associated with the working version of the experiment
            experiment_id = self.experiment.working_version_id
            experiment_version_number = self.experiment.version_number

        # Create database trace record
        self.trace_record = await self._acreate_trace(trace_context, session, experiment_id, experiment_version_number)

        self.start_time = time.time()

        try:
            yield trace_context
        except Exception:
            self.error_detected = True
            raise
        finally:
            # Guaranteed cleanup - update trace duration and status
            if self.trace_record and self.start_time:
                try:
                    end_time = time.time()
                    duration = end_time - self.start_time
                    duration_ms = int(duration * 1000)

                    self.trace_record.duration = duration_ms
                    if self.error_detected:
                        self.trace_record.status = TraceStatus.ERROR
                    else:
                        self.trace_record.status = TraceStatus.SUCCESS

                    # Note: OCSTracer doesn't store trace outputs in database
                    # but could access them via trace_context.outputs if needed
                    await self.trace_record.asave()
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
                        self.trace_record.output_message_id,
                    )

            # Reset state
            self.trace_record = None
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

    @asynccontextmanager
    async def aspan(
        self,
        span_context: TraceContext,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> AsyncIterator[TraceContext]:
        with self.span(span_context, inputs, metadata, level) as span:
            yield span

    def get_langchain_callback(self) -> None:
        """Return a mock callback handler since OCS tracer doesn't need LangChain integration."""
        return OCSCallbackHandler(tracer=self)

    def add_trace_tags(self, tags: list[str]) -> None:
        pass

    def set_output_message_id(self, output_message_id: str) -> None:
        """Set the output message ID for the trace."""
        if self.trace_record:
            self.trace_record.output_message_id = output_message_id

    def set_input_message_id(self, input_message_id: str) -> None:
        """Set the input message ID for the trace."""
        if self.trace_record:
            self.trace_record.input_message_id = input_message_id

    def get_trace_metadata(self) -> dict[str, Any]:
        if not self.ready:
            return

        return {
            "trace_id": self.trace_record.id,
            "trace_url": self.trace_record.get_absolute_url(self.team_slug),
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
    def __init__(self, tracer: OCSTracer):
        super().__init__()
        self.tracer = tracer

    def on_llm_error(self, *args, **kwargs) -> None:
        self.tracer.error_detected = True

    def on_chain_error(self, *args, **kwargs) -> None:
        self.tracer.error_detected = True

    def on_tool_error(self, *args, **kwargs) -> None:
        self.tracer.error_detected = True
