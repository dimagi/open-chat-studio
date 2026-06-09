from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.cost_tracking.models import Confidence, PricingRule, ServiceKind, UsageRecord
from apps.cost_tracking.services.recorder import UsageEvent, record_usage_bulk


def _event(qty=1000, kind=ServiceKind.LLM_INPUT, **overrides):
    return UsageEvent(
        service_kind=kind,
        provider_type=overrides.pop("provider_type", "openai"),
        model_name=overrides.pop("model_name", "test-model"),
        quantity=qty,
        confidence=overrides.pop("confidence", Confidence.EXACT),
        extra=overrides.pop("extra", None),
    )


@pytest.mark.django_db()
def test_empty_events_short_circuits(team):
    with patch.object(UsageRecord.objects, "bulk_create") as bulk:
        record_usage_bulk([], team_id=team.id)
        assert bulk.call_count == 0
    assert UsageRecord.objects.count() == 0


@pytest.mark.django_db()
def test_priced_event_writes_row_with_computed_cost(team):
    PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00015"),
    )
    record_usage_bulk([_event(qty=1000)], team_id=team.id)

    row = UsageRecord.objects.get()
    # 1000 tokens / 1000 = 1 unit; 1 * 0.00015 = 0.00015
    assert row.cost == Decimal("0.00015000")
    assert row.unit_price == Decimal("0.00015000")
    assert row.confidence == Confidence.EXACT
    assert row.pricing_rule_id is not None


@pytest.mark.django_db()
def test_unpriced_event_writes_row_with_zero_cost(team):
    record_usage_bulk([_event(qty=1000)], team_id=team.id)

    row = UsageRecord.objects.get()
    assert row.cost == Decimal("0")
    assert row.unit_price is None
    assert row.pricing_rule_id is None


@pytest.mark.django_db()
def test_bulk_create_issued_once_per_call(team):
    PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00015"),
    )
    PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_OUTPUT,
        unit_price=Decimal("0.00060"),
    )

    events = [
        _event(qty=1000, kind=ServiceKind.LLM_INPUT),
        _event(qty=500, kind=ServiceKind.LLM_OUTPUT),
    ]

    with patch.object(UsageRecord.objects, "bulk_create", wraps=UsageRecord.objects.bulk_create) as bulk:
        record_usage_bulk(events, team_id=team.id)
        assert bulk.call_count == 1
        assert len(bulk.call_args[0][0]) == 2

    assert UsageRecord.objects.count() == 2


@pytest.mark.django_db()
def test_exception_in_bulk_create_is_swallowed(team, caplog):
    PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00015"),
    )

    with patch.object(UsageRecord.objects, "bulk_create", side_effect=RuntimeError("simulated db hiccup")):
        # Must NOT raise — a DB failure can't break the LLM/tracer path.
        record_usage_bulk([_event(qty=1000)], team_id=team.id)

    assert "cost_tracking.bulk_insert_failed" in caplog.text
    assert UsageRecord.objects.count() == 0


@pytest.mark.django_db()
def test_event_extra_is_preserved_on_the_row(team):
    record_usage_bulk(
        [_event(qty=1000, extra={"estimator": "tiktoken"}, confidence=Confidence.ESTIMATED)],
        team_id=team.id,
    )
    row = UsageRecord.objects.get()
    assert row.extra == {"estimator": "tiktoken"}
    assert row.confidence == Confidence.ESTIMATED
