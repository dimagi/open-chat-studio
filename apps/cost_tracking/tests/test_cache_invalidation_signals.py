from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cost_tracking.models import PricingRule, ServiceKind
from apps.cost_tracking.services.pricing import PricingResolver


def _warm_cache_for(provider="openai", model="test-model", kind=ServiceKind.LLM_INPUT):
    PricingResolver().resolve(
        team_id=None,
        provider_type=provider,
        model_name=model,
        service_kind=kind,
        at=timezone.now(),
    )


@pytest.mark.django_db()
def test_post_save_signal_invalidates_cache():
    rule = PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00015"),
    )
    _warm_cache_for()  # cache now holds the resolved rule

    # Saving the rule should fire post_save -> invalidate cache.
    with patch.object(PricingResolver, "invalidate", wraps=PricingResolver.invalidate) as invalidate:
        rule.notes = "touched"
        rule.save()
        assert invalidate.call_count >= 1
        invalidate.assert_any_call(
            team_id=None,
            provider_type="openai",
            model_name="test-model",
            service_kind=ServiceKind.LLM_INPUT,
        )


@pytest.mark.django_db()
def test_post_delete_signal_invalidates_cache():
    rule = PricingRule.objects.create(
        team=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00015"),
    )
    _warm_cache_for()

    with patch.object(PricingResolver, "invalidate", wraps=PricingResolver.invalidate) as invalidate:
        rule.delete()
        assert invalidate.call_count >= 1
        invalidate.assert_any_call(
            team_id=None,
            provider_type="openai",
            model_name="test-model",
            service_kind=ServiceKind.LLM_INPUT,
        )


@pytest.mark.django_db()
def test_save_signal_uses_correct_team_id_for_team_scoped_rule(team):
    rule = PricingRule.objects.create(
        team=team,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        unit_price=Decimal("0.00005"),
        source="manual",
    )

    with patch.object(PricingResolver, "invalidate", wraps=PricingResolver.invalidate) as invalidate:
        rule.notes = "touched"
        rule.save()
        invalidate.assert_any_call(
            team_id=team.id,
            provider_type="openai",
            model_name="test-model",
            service_kind=ServiceKind.LLM_INPUT,
        )
