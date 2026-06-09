"""Pricing resolver. Three-step lookup (team override -> global rule -> unpriced
sentinel), cached in Redis with a 24h TTL. Invalidated by signals on PricingRule
save/delete; the TTL is just a safety net.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Q

from apps.cost_tracking.models import PricingRule


@dataclass(frozen=True)
class PricingKey:
    """Identifies the (team, provider, model, service_kind) tuple that maps to
    a single active PricingRule. `team_id=None` means a global lookup.
    """

    provider_type: str
    model_name: str
    service_kind: str
    team_id: int | None = None


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

    def resolve(self, key: PricingKey, at: datetime, use_cache: bool = True) -> ResolvedRule:
        """Resolve the active rule for `key` at `at`.

        Lookup order: team override -> global rule -> UNPRICED sentinel.
        `use_cache=False` skips Redis (used by tests doing historical time-travel).
        """
        if key.team_id is not None:
            team_rule = self._lookup_one(key, at, use_cache)
            if team_rule.unit_price is not None:
                return team_rule
        return self._lookup_one(replace(key, team_id=None), at, use_cache)

    @classmethod
    def invalidate(cls, key: PricingKey) -> None:
        cache.delete(cls._cache_key(key))

    def _lookup_one(self, key: PricingKey, at: datetime, use_cache: bool) -> ResolvedRule:
        cache_key = self._cache_key(key)
        if use_cache:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        qs = PricingRule.objects.filter(
            provider_type=key.provider_type,
            model_name=key.model_name,
            service_kind=key.service_kind,
            effective_from__lte=at,
        ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
        qs = qs.filter(team__isnull=True) if key.team_id is None else qs.filter(team_id=key.team_id)
        rule = qs.order_by("-effective_from").first()

        resolved = (
            ResolvedRule(unit_price=rule.unit_price, currency=rule.currency, pricing_rule_id=rule.pk)
            if rule
            else UNPRICED
        )
        if use_cache:
            cache.set(cache_key, resolved, self.CACHE_TTL_SECONDS)
        return resolved

    @classmethod
    def _cache_key(cls, key: PricingKey) -> str:
        scope = "global" if key.team_id is None else str(key.team_id)
        return f"{cls.CACHE_KEY_PREFIX}:{scope}:{key.provider_type}:{key.model_name}:{key.service_kind}"
