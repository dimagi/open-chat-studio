"""A tracer that bills a run's LLM calls without keeping a trace of its own.

Evaluation runs deliberately produce no `Trace` rows — one per evaluated message would
flood the team's trace list, and `Trace.session` is `SET_NULL`, so they would outlive the
eval sessions `cleanup_old_evaluation_data` prunes. The money is real either way, so this
tracer keeps the half of `OCSTracer` that bills (the `MetricsCollector` and the drain into
`UsageRecord`) and drops the rest. See ADR-0049.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler

from apps.cost_tracking.services.recorder import UsageContext, record_usage_bulk
from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing.const import USAGE_ONLY_TRACE_PROVIDER, SpanLevel
from apps.service_providers.tracing.metrics import MetricsCollector

from .base import ServiceNotInitializedException, TraceContext, Tracer

logger = logging.getLogger("ocs.tracing")


class UsageOnlyTracer(Tracer):
    """Charges everything an LLM does inside the trace to `context`.

    `session_id` is filled in from the session the trace ran under, so a caller supplies
    only the attribution it knows up front (team, source, experiment, and for evaluations
    the config). `event_extra` is merged into every row's `extra` — where the evaluation
    run id goes, since runs get pruned and can't be an FK.
    """

    def __init__(self, context: UsageContext, event_extra: dict | None = None):
        super().__init__(USAGE_ONLY_TRACE_PROVIDER, {})
        self.context = context
        self.event_extra = event_extra or {}
        self.metrics_collector: MetricsCollector | None = None

    @property
    def ready(self) -> bool:
        return self.metrics_collector is not None

    @contextmanager
    def trace(
        self,
        trace_context: TraceContext,
        session: ExperimentSession | None,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[TraceContext]:
        """Collect usage for the duration of the trace, then bill it."""
        self.trace_name = trace_context.name
        self.trace_id = trace_context.id
        self.session = session
        self.metrics_collector = MetricsCollector(start_time=time.time())
        try:
            yield trace_context
        finally:
            self._record_costs()
            self._reset()

    def _record_costs(self) -> None:
        """Drain the collector's accumulated usage into UsageRecord rows.

        Never raises: `record_usage_bulk` swallows insert failures, but pricing resolution
        sits outside its guard and this runs from a `finally`, where an exception would
        mask whatever the traced code was doing.
        """
        try:
            if not self.metrics_collector:
                return
            events = list(self.metrics_collector.iter_cost_events())
            if not events:
                return
            for event in events:
                event.extra = {**(event.extra or {}), **self.event_extra}
            record_usage_bulk(events, replace(self.context, session_id=self.session.id if self.session else None))
        except Exception:
            logger.exception("Failed to record usage", extra={"team_id": self.context.team_id})

    def _reset(self) -> None:
        self.trace_id = None
        self.trace_name = None
        self.session = None
        self.metrics_collector = None

    @contextmanager
    def span(
        self,
        span_context: TraceContext,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        level: SpanLevel = "DEFAULT",
    ) -> Iterator[TraceContext]:
        """No-op: spans carry no billing information of their own."""
        yield span_context

    def get_langchain_callback(self) -> BaseCallbackHandler:
        """The collector itself — it is a callback handler, and usage is all this records."""
        if self.metrics_collector is None:
            raise ServiceNotInitializedException("No active trace")
        return self.metrics_collector

    def set_session(self, session: ExperimentSession) -> None:
        self.session = session

    def add_trace_tags(self, tags: list[str]) -> None:
        pass

    def set_output_message_id(self, output_message_id: str) -> None:
        pass

    def set_input_message_id(self, input_message_id: str) -> None:
        pass

    def set_participant_data_diff(self, diff: list[tuple[str, str | list, Any]]) -> None:
        pass
