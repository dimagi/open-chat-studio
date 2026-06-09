"""Pricing resolver. Three-step lookup (team override -> global rule -> unpriced
sentinel), cached in Redis with a 24h TTL. Invalidated by signals on PricingRule
save/delete; the TTL is just a safety net.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Q

from apps.cost_tracking.models import PricingRule


@dataclass(frozen=True)
class ResolvedRule:
    """What the resolver returns. `unit_price=None` means no rule matched
    (callers infer "unpriced" from that).
    """

    unit_price: Decimal | None
    currency: str
    pricing_rule_id: int | None


UNPRICED = ResolvedRule(unit_price=None, currency="USD", pricing_rule_id=None)


class PricingResolver:
    CACHE_TTL_SECONDS = 24 * 60 * 60  # safety net; signals do the real invalidation
    CACHE_KEY_PREFIX = "cost_tracking:pricing"

    def resolve(
        self,
        team_id: int | None,
        provider_type: str,
        model_name: str,
        service_kind: str,
        at: datetime,
        use_cache: bool = True,
    ) -> ResolvedRule:
        """Resolve the active rule for (team, provider, model, service_kind) at `at`.

        Lookup order: team override -> global rule -> UNPRICED sentinel.
        `use_cache=False` skips Redis (used by tests doing historical time-travel).
        """
        if team_id is not None:
            team_rule = self._lookup_one(team_id, provider_type, model_name, service_kind, at, use_cache)
            if team_rule.unit_price is not None:
                return team_rule
        return self._lookup_one(None, provider_type, model_name, service_kind, at, use_cache)

    @classmethod
    def invalidate(
        cls,
        team_id: int | None,
        provider_type: str,
        model_name: str,
        service_kind: str,
    ) -> None:
        cache.delete(cls._cache_key(team_id, provider_type, model_name, service_kind))

    def _lookup_one(
        self,
        team_id: int | None,
        provider_type: str,
        model_name: str,
        service_kind: str,
        at: datetime,
        use_cache: bool,
    ) -> ResolvedRule:
        key = self._cache_key(team_id, provider_type, model_name, service_kind)
        if use_cache:
            cached = cache.get(key)
            if cached is not None:
                return cached

        qs = PricingRule.objects.filter(
            provider_type=provider_type,
            model_name=model_name,
            service_kind=service_kind,
            effective_from__lte=at,
        ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
        qs = qs.filter(team__isnull=True) if team_id is None else qs.filter(team_id=team_id)
        rule = qs.order_by("-effective_from").first()

        resolved = (
            ResolvedRule(unit_price=rule.unit_price, currency=rule.currency, pricing_rule_id=rule.pk)
            if rule
            else UNPRICED
        )
        if use_cache:
            cache.set(key, resolved, self.CACHE_TTL_SECONDS)
        return resolved

    @classmethod
    def _cache_key(cls, team_id: int | None, provider_type: str, model_name: str, service_kind: str) -> str:
        scope = "global" if team_id is None else str(team_id)
        return f"{cls.CACHE_KEY_PREFIX}:{scope}:{provider_type}:{model_name}:{service_kind}"
