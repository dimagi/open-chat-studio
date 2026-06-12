from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cost_tracking.models import PricingRule, ServiceKind
from apps.cost_tracking.services.pricing import PricingKey, PricingResolver

KEY = PricingKey(provider_type="openai", model_name="test-model", service_kind=ServiceKind.LLM_INPUT)


def _warm_cache_for(key: PricingKey = KEY) -> None:
    PricingResolver().resolve(key, at=timezone.now())


@pytest.mark.django_db()
def test_post_save_signal_invalidates_cache():
    rule = PricingRule.objects.create(
        team=None,
        provider_type=KEY.provider_type,
        model_name=KEY.model_name,
        service_kind=KEY.service_kind,
        unit_price=Decimal("0.00015"),
    )
    _warm_cache_for()

    with patch.object(PricingResolver, "invalidate", wraps=PricingResolver.invalidate) as invalidate:
        rule.notes = "touched"
        rule.save()
        assert invalidate.call_count >= 1
        invalidate.assert_any_call(KEY)


@pytest.mark.django_db()
def test_post_delete_signal_invalidates_cache():
    rule = PricingRule.objects.create(
        team=None,
        provider_type=KEY.provider_type,
        model_name=KEY.model_name,
        service_kind=KEY.service_kind,
        unit_price=Decimal("0.00015"),
    )
    _warm_cache_for()

    with patch.object(PricingResolver, "invalidate", wraps=PricingResolver.invalidate) as invalidate:
        rule.delete()
        assert invalidate.call_count >= 1
        invalidate.assert_any_call(KEY)


@pytest.mark.django_db()
def test_save_signal_uses_correct_team_id_for_team_scoped_rule(team):
    rule = PricingRule.objects.create(
        team=team,
        provider_type=KEY.provider_type,
        model_name=KEY.model_name,
        service_kind=KEY.service_kind,
        unit_price=Decimal("0.00005"),
        source="manual",
    )

    with patch.object(PricingResolver, "invalidate", wraps=PricingResolver.invalidate) as invalidate:
        rule.notes = "touched"
        rule.save()
        invalidate.assert_any_call(replace(KEY, team_id=team.id))
