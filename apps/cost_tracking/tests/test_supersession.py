from decimal import Decimal

import pytest

from apps.cost_tracking.management.commands.load_ai_pricing import upsert_global_rule
from apps.cost_tracking.models import PricingRule, ServiceKind
from apps.cost_tracking.services.pricing import PricingKey

KEY = PricingKey(provider_type="openai", model_name="test-model", service_kind=ServiceKind.LLM_INPUT)


def _key_filter(**extra) -> dict:
    """Filter kwargs that match the exact (global, provider, model, kind) under
    test. Used by the helpers below to avoid collisions with seed rows."""
    return {
        "team__isnull": True,
        "provider_type": KEY.provider_type,
        "model_name": KEY.model_name,
        "service_kind": KEY.service_kind,
        **extra,
    }


def _active_count():
    return PricingRule.objects.filter(**_key_filter(effective_to__isnull=True)).count()


def _all_count():
    return PricingRule.objects.filter(**_key_filter()).count()


@pytest.mark.django_db()
def test_first_upsert_creates_a_new_rule():
    outcome = upsert_global_rule(KEY, unit_price=Decimal("0.00015"))
    assert outcome == "created"
    assert _active_count() == 1
    assert _all_count() == 1


@pytest.mark.django_db()
def test_upsert_with_same_rate_is_a_noop():
    upsert_global_rule(KEY, unit_price=Decimal("0.00015"))
    outcome = upsert_global_rule(KEY, unit_price=Decimal("0.00015"))
    assert outcome == "unchanged"
    assert _active_count() == 1
    assert _all_count() == 1


@pytest.mark.django_db()
def test_upsert_with_different_rate_supersedes():
    upsert_global_rule(KEY, unit_price=Decimal("0.00015"))
    outcome = upsert_global_rule(KEY, unit_price=Decimal("0.00020"))
    assert outcome == "superseded"
    # Old row closed, new row active. Two rows total, one active.
    assert _active_count() == 1
    assert _all_count() == 2

    active = PricingRule.objects.get(**_key_filter(effective_to__isnull=True))
    assert active.unit_price == Decimal("0.00020")

    closed = PricingRule.objects.get(**_key_filter(effective_to__isnull=False))
    assert closed.unit_price == Decimal("0.00015")
    assert closed.effective_to is not None


@pytest.mark.django_db()
def test_supersession_respects_active_rule_unique_constraint():
    """Sanity check: after a supersession, the partial unique constraint still
    holds (exactly one active rule per key)."""
    for price in ["0.00015", "0.00020", "0.00025", "0.00030"]:
        upsert_global_rule(KEY, unit_price=Decimal(price))
    assert _active_count() == 1
    assert _all_count() == 4
    active = PricingRule.objects.get(**_key_filter(effective_to__isnull=True))
    assert active.unit_price == Decimal("0.00030")
