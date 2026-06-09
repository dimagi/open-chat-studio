"""Invalidate PricingResolver cache on PricingRule mutations.

These signals fire for Model.save() and Model.delete() but NOT for queryset
.update() / .delete() — those code paths must call PricingResolver.invalidate(...)
explicitly. The load_ai_pricing loader does this when closing a rule via .update().
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.cost_tracking.models import PricingRule
from apps.cost_tracking.services.pricing import PricingKey, PricingResolver


def _key_for(instance: PricingRule) -> PricingKey:
    return PricingKey(
        provider_type=instance.provider_type,
        model_name=instance.model_name,
        service_kind=instance.service_kind,
        team_id=instance.team_id,
    )


@receiver(post_save, sender=PricingRule)
def _invalidate_on_save(sender, instance: PricingRule, **kwargs):
    PricingResolver.invalidate(_key_for(instance))


@receiver(post_delete, sender=PricingRule)
def _invalidate_on_delete(sender, instance: PricingRule, **kwargs):
    PricingResolver.invalidate(_key_for(instance))
