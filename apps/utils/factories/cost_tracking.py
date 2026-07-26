"""Test factories for cost-tracking models. `at=<datetime>` is a custom
post_generation hook that overrides `UsageRecord.timestamp` (which uses
auto_now_add and can't be set via .create()) by issuing a direct .update().
"""

from decimal import Decimal

import factory
import factory.django

from apps.cost_tracking.models import Confidence, PricingRule, PricingSource, ServiceKind, UsageRecord
from apps.utils.factories.team import TeamFactory


class PricingRuleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PricingRule

    team = None
    provider_type = "openai"
    model_name = factory.Sequence(lambda n: f"test-model-{n}")
    service_kind = ServiceKind.LLM_INPUT
    unit_price = Decimal("0.00015")
    source = PricingSource.SEED


class UsageRecordFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UsageRecord
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    service_kind = ServiceKind.LLM_INPUT
    provider_type = "openai"
    model_name = "gpt-4o-mini"
    quantity = 100
    unit_price = Decimal("0.00015")
    cost = Decimal(0)
    confidence = Confidence.EXACT

    @factory.post_generation
    def at(self, create, extracted, **kwargs):
        """Stamp `timestamp` to a specific moment after creation. Skips when
        not provided; tests that don't care about timing get auto_now_add."""
        if not create or extracted is None:
            return
        UsageRecord.objects.filter(pk=self.pk).update(timestamp=extracted)
        self.refresh_from_db()
