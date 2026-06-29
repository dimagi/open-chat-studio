from decimal import Decimal

import factory
import factory.django

from apps.cost_tracking.models import PricingRule, ServiceKind, UsageRecord
from apps.utils.factories.team import TeamFactory


class PricingRuleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PricingRule

    team = factory.SubFactory(TeamFactory)
    provider_type = "openai"
    model_name = factory.Sequence(lambda n: f"gpt-{n}")
    service_kind = ServiceKind.LLM_INPUT
    unit_price = Decimal("0.00000100")


class UsageRecordFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UsageRecord

    team = factory.SubFactory(TeamFactory)
    service_kind = ServiceKind.LLM_INPUT
    provider_type = "openai"
    model_name = "gpt-4"
    quantity = Decimal("100")
    unit_price = Decimal("0.00000100")
    cost = Decimal("0.0001")
