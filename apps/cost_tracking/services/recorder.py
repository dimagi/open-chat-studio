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
from apps.cost_tracking.services.pricing import PricingResolver

logger = logging.getLogger(__name__)

_CENT_QUANTUM = Decimal("0.00000001")
_THOUSAND = Decimal(1000)


@dataclass
class UsageEvent:
    """One bucket from the collector. Trace-scoped context (team_id, trace,
    experiment_id, session, participant) is supplied once by the caller and
    shared across all events from a single trace.
    """

    service_kind: ServiceKind
    provider_type: str
    model_name: str
    quantity: Decimal | int
    confidence: Confidence = Confidence.EXACT
    extra: dict | None = None


def record_usage_bulk(
    events: list[UsageEvent],
    *,
    team_id: int,
    trace_id: int | None = None,
    experiment_id: int | None = None,
    session_id: int | None = None,
    participant_id: int | None = None,
) -> None:
    """Resolve pricing per event, build UsageRecord rows, bulk-insert in one
    statement inside a transaction. Never raises — a DB hiccup must not
    propagate back into the LLM/tracer path; failures are logged with a counter.
    """
    if not events:
        return

    resolver = PricingResolver()
    now = timezone.now()
    rows: list[UsageRecord] = []

    for event in events:
        resolved = resolver.resolve(
            team_id=team_id,
            provider_type=event.provider_type,
            model_name=event.model_name,
            service_kind=event.service_kind,
            at=now,
        )
        priced = resolved.unit_price is not None
        cost = (
            (Decimal(event.quantity) / _THOUSAND * resolved.unit_price).quantize(_CENT_QUANTUM)
            if priced
            else Decimal("0")
        )
        rows.append(
            UsageRecord(
                team_id=team_id,
                service_kind=event.service_kind,
                provider_type=event.provider_type,
                model_name=event.model_name,
                quantity=event.quantity,
                unit_price=resolved.unit_price,  # None for unpriced
                cost=cost,
                currency=resolved.currency,
                confidence=event.confidence,
                pricing_rule_id=resolved.pricing_rule_id,
                experiment_id=experiment_id,
                session_id=session_id,
                participant_id=participant_id,
                trace_id=trace_id,
                extra=event.extra or {},
            )
        )

    try:
        with transaction.atomic():
            UsageRecord.objects.bulk_create(rows)
    except Exception:
        logger.exception("cost_tracking.bulk_insert_failed", extra={"team_id": team_id, "n_events": len(rows)})
