"""Cost tracking for an evaluation run's two halves — the judge calls and the bot
generation they score. Both are the team's spend and neither is a chatbot's, so both are
recorded with `source=EVALUATION` (ADR-0048, ADR-0049).

Judge-model calls run outside the chat/pipeline path, so no tracer would drain their
token usage: this module attaches a MetricsCollector to the evaluator's own LLM call and
writes the rows itself. Bot generation does run through the pipeline, but with tracing
switched off for eval channels, so it gets a `UsageOnlyTracer` — which bills without
leaving a `Trace` behind.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.callbacks.base import BaseCallbackHandler

from apps.cost_tracking.models import UsageSource
from apps.cost_tracking.services.recorder import UsageContext, record_usage_bulk
from apps.service_providers.tracing.metrics import MetricsCollector
from apps.service_providers.tracing.usage_tracer import UsageOnlyTracer

if TYPE_CHECKING:
    from apps.evaluations.models import EvaluationRun
    from apps.experiments.models import Experiment

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


def generation_usage_tracer(experiment: "Experiment", evaluation_run: "EvaluationRun") -> UsageOnlyTracer:
    """The tracer that bills the bot generation an evaluation run drives.

    `experiment` is resolved to its working version, matching how OCSTracer attributes
    chat traffic and how `_usage_context_for` attributes the judge calls scoring this
    generation, so both halves of a run's spend land on one chatbot. The session is
    filled in by the tracer once the trace opens.
    """
    return UsageOnlyTracer(
        UsageContext(
            team_id=evaluation_run.team_id,
            source=UsageSource.EVALUATION,
            experiment_id=experiment.get_working_version_id(),
            evaluation_config_id=evaluation_run.config_id,
        ),
        event_extra={"evaluation_run_id": evaluation_run.id},
    )


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
