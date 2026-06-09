"""Verify the (quantity / 1000) * unit_price math is exact at Decimal precision
and no float drift sneaks in.
"""

import random
from decimal import Decimal

import pytest
from django.db.models import Sum

from apps.cost_tracking.models import PricingRule, ServiceKind
from apps.cost_tracking.services.recorder import TraceContext, UsageEvent, record_usage_bulk


@pytest.mark.parametrize(
    ("qty", "unit_price", "expected_cost"),
    [
        # 1000 tokens at $0.00025/1K -> exactly $0.00025
        (1000, "0.00025", "0.00025000"),
        # 500 tokens at $0.00025/1K -> exactly $0.000125
        (500, "0.00025", "0.00012500"),
        # 1 token at $0.00025/1K -> $0.00000025 (fits in 8 decimal places)
        (1, "0.00025", "0.00000025"),
        # Headline OpenAI gpt-4o-mini rate over 10k tokens
        (10000, "0.00015", "0.00150000"),
    ],
)
@pytest.mark.django_db()
def test_cost_calc_is_exact(team, qty, unit_price, expected_cost):
    PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal(unit_price),
    )
    record_usage_bulk(
        [
            UsageEvent(
                service_kind=ServiceKind.LLM_INPUT,
                provider_type="openai",
                model_name="test-model",
                quantity=qty,
            )
        ],
        TraceContext(team_id=team.id),
    )
    row_cost = team.usagerecord_set.get().cost
    assert row_cost == Decimal(expected_cost)


@pytest.mark.django_db()
def test_summed_costs_have_no_float_drift(team):
    """1000 random small charges summed via SQL match an independent Decimal
    reference computed in Python."""
    PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00015"),
    )

    rng = random.Random(42)
    events = [
        UsageEvent(
            service_kind=ServiceKind.LLM_INPUT,
            provider_type="openai",
            model_name="test-model",
            quantity=rng.randint(10, 5000),
        )
        for _ in range(1000)
    ]

    record_usage_bulk(events, TraceContext(team_id=team.id))

    expected = sum(
        (Decimal(e.quantity) / Decimal(1000) * Decimal("0.00015")).quantize(Decimal("0.00000001")) for e in events
    )
    actual = team.usagerecord_set.aggregate(total=Sum("cost"))["total"]
    assert actual == expected
