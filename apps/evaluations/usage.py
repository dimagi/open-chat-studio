"""Cost tracking for evaluator LLM calls.

Judge-model calls run outside the chat/pipeline path, so there is no OCSTracer
trace whose finalisation would drain their token usage into UsageRecord rows.
This module attaches a MetricsCollector to the evaluator's own LLM call and
writes the rows itself, tagging each one so eval spend stays distinguishable
from the chat traffic recorded by the tracer.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from langchain_core.callbacks.base import BaseCallbackHandler

from apps.cost_tracking.models import UsageSource
from apps.cost_tracking.services.recorder import UsageContext, record_usage_bulk
from apps.service_providers.tracing.metrics import MetricsCollector

logger = logging.getLogger("ocs.evaluations")


@dataclass(frozen=True)
class EvaluatorUsageContext:
    """Attribution for the usage a single evaluator run produces.

    `evaluation_config_id` is the durable "whose eval was this" link; the run id is
    recorded in `extra` because runs are pruned. `experiment_id`/`session_id` point at
    the bot generation the judge call is scoring so both halves of a run's spend group
    together; they stay None when the run has no generation experiment — which is every
    session-mode run, since generation is not supported for session-mode datasets.
    `participant` is left off deliberately: it's only ever the synthetic evaluations
    participant, and reading it back would cost a query per evaluator per message.
    """

    team_id: int
    evaluation_run_id: int
    evaluation_config_id: int | None = None
    experiment_id: int | None = None
    session_id: int | None = None


@contextmanager
def track_evaluator_usage(context: EvaluatorUsageContext | None) -> Iterator[list[BaseCallbackHandler]]:
    """Yield the callbacks to hand to an evaluator's LLM call, then record what
    they collected.

    Yields an empty list when `context` is None (an evaluator invoked outside a
    run) so callers need no branching. Usage is recorded even when the call ends
    in an exception: retried and schema-rejected responses were still billed.
    """
    if context is None:
        yield []
        return

    collector = MetricsCollector(start_time=time.time())
    try:
        yield [collector]
    finally:
        _record(collector, context)


def _record(collector: MetricsCollector, context: EvaluatorUsageContext) -> None:
    """Write the collector's accumulated usage as UsageRecord rows.

    Never raises. `record_usage_bulk` already swallows insert failures, but
    pricing resolution sits outside its guard, and this runs from a `finally`
    where an exception would mask the evaluator's own.
    """
    try:
        events = list(collector.iter_cost_events())
        for event in events:
            event.extra = {**(event.extra or {}), "evaluation_run_id": context.evaluation_run_id}
        record_usage_bulk(
            events,
            UsageContext(
                team_id=context.team_id,
                source=UsageSource.EVALUATION,
                evaluation_config_id=context.evaluation_config_id,
                experiment_id=context.experiment_id,
                session_id=context.session_id,
            ),
        )
    except Exception:
        logger.exception(
            "Failed to record evaluator usage for run %s",
            context.evaluation_run_id,
            extra={"team_id": context.team_id},
        )
