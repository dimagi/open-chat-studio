"""Verify the partial unique constraint with nulls_distinct=False is in
effect at the database level — without it, two `team=NULL` rows for the same
(provider, model, service_kind) would both be allowed active.
"""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.cost_tracking.models import PricingRule, ServiceKind


@pytest.mark.django_db()
def test_two_active_global_rules_rejected_by_db():
    PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00015"),
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PricingRule.objects.create(
                team=None,
                provider_type="openai",
                model_name="test-model",
                service_kind=ServiceKind.LLM_INPUT,
                unit_price=Decimal("0.00020"),
            )


@pytest.mark.django_db()
def test_team_scoped_rule_does_not_collide_with_global(team):
    PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00015"),
    )

    # Different team_id -> allowed concurrently with the global rule.
    PricingRule.objects.create(
        team=team,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00005"),
    )

    assert PricingRule.objects.filter(model_name="test-model", effective_to__isnull=True).count() == 2


@pytest.mark.django_db()
def test_closed_rule_does_not_block_new_active_rule():
    """A row with effective_to set is outside the partial index and shouldn't
    conflict with a new active row for the same key."""
    PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00015"),
        effective_to=timezone.now(),
    )

    PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00020"),
    )

    assert PricingRule.objects.filter(team__isnull=True, model_name="test-model").count() == 2
