"""Write path for cost tracking. `record_usage_bulk` is called once per trace
at finalisation with the events accumulated by the MetricsCollector (PR 2).

Cost calc: `(quantity / 1000) * unit_price`. Quantity is raw tokens; unit_price
is per 1K tokens (canonical for every v4 service kind).
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.cost_tracking.models import Confidence, ServiceKind, UsageRecord
from apps.cost_tracking.services.pricing import PricingKey, PricingResolver

logger = logging.getLogger(__name__)

_CENT_QUANTUM = Decimal("0.00000001")
_THOUSAND = Decimal(1000)


@dataclass
class UsageEvent:
    """One bucket from the collector. Trace-scoped context is supplied
    separately as `TraceContext` and shared across all events from a trace.
    """

    service_kind: ServiceKind
    provider_type: str
    model_name: str
    quantity: Decimal | int
    confidence: Confidence = Confidence.EXACT
    extra: dict | None = None


@dataclass
class TraceContext:
    """Trace-scoped context that's the same for every UsageEvent emitted from
    a single trace. Passed once to `record_usage_bulk` rather than carried on
    each event.
    """

    team_id: int
    trace_id: int | None = None
    experiment_id: int | None = None
    session_id: int | None = None
    participant_id: int | None = None


def record_usage_bulk(events: list[UsageEvent], ctx: TraceContext) -> None:
    """Resolve pricing per event, build UsageRecord rows, bulk-insert in one
    statement inside a transaction. Never raises — a DB hiccup must not
    propagate back into the LLM/tracer path; failures are logged.
    """
    if not events:
        return

    resolver = PricingResolver()
    now = timezone.now()
    rows: list[UsageRecord] = []

    for event in events:
        resolved = resolver.resolve(
            PricingKey(
                team_id=ctx.team_id,
                provider_type=event.provider_type,
                model_name=event.model_name,
                service_kind=event.service_kind,
            ),
            at=now,
        )
        # cost stays at 0 unless we have BOTH a priced rule AND a real quantity.
        # `event.quantity` is typed as Decimal | int but we still guard against
        # None / 0 so the future UNKNOWN-confidence path (no token count) can
        # land here cleanly without a Decimal(None) crash.
        priced = resolved.unit_price is not None
        has_quantity = event.quantity is not None and event.quantity != 0
        cost = (
            (Decimal(event.quantity) / _THOUSAND * resolved.unit_price).quantize(_CENT_QUANTUM)
            if priced and has_quantity
            else Decimal("0")
        )
        rows.append(
            UsageRecord(
                team_id=ctx.team_id,
                service_kind=event.service_kind,
                provider_type=event.provider_type,
                model_name=event.model_name,
                quantity=event.quantity,
                unit_price=resolved.unit_price,  # None for unpriced
                cost=cost,
                currency=resolved.currency,
                confidence=event.confidence,
                pricing_rule_id=resolved.pricing_rule_id,
                experiment_id=ctx.experiment_id,
                session_id=ctx.session_id,
                participant_id=ctx.participant_id,
                trace_id=ctx.trace_id,
                extra=event.extra or {},
            )
        )

    try:
        with transaction.atomic():
            UsageRecord.objects.bulk_create(rows)
    except Exception:
        logger.exception("cost_tracking.bulk_insert_failed", extra={"team_id": ctx.team_id, "n_events": len(rows)})
