"""Pricing resolver. Three-step lookup (team override -> global rule -> unpriced
sentinel), cached in Redis with a 24h TTL. Invalidated by signals on PricingRule
save/delete; the TTL is just a safety net.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

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
    # The cache key intentionally omits `at`; the cache is correct only for
    # "active right now" lookups. Resolves with an `at` further than this
    # window from now bypass the cache automatically so historical/future
    # queries can't accidentally hit a stale "current" entry.
    CACHE_AT_SKEW = timedelta(minutes=1)

    def resolve(self, key: PricingKey, at: datetime, use_cache: bool = True) -> ResolvedRule:
        """Resolve the active rule for `key` at `at`.

        Lookup order: team override -> global rule -> UNPRICED sentinel.
        `use_cache=False` skips Redis explicitly; the cache is also bypassed
        automatically when `at` is too far from now (see `_is_near_now`).
        """
        cache_ok = use_cache and self._is_near_now(at)
        team_rule = self._maybe_team_rule(key, at, cache_ok)
        if team_rule is not None:
            return team_rule
        return self._lookup_one(replace(key, team_id=None), at, cache_ok)

    def _is_near_now(self, at: datetime) -> bool:
        """The cache only stores "active right now" results. Historical or
        future `at` queries can't be safely served from it, so we bypass.
        """
        now = timezone.now()
        return now - self.CACHE_AT_SKEW <= at <= now + self.CACHE_AT_SKEW

    def _maybe_team_rule(self, key: PricingKey, at: datetime, use_cache: bool) -> ResolvedRule | None:
        """Return the team-scoped rule if one is set and matched, else None.

        Returning `None` (rather than the UNPRICED sentinel) lets `resolve()`
        cleanly distinguish "no team override exists" from "found a team
        override that happens to be unpriced" — only the former should fall
        through to the global lookup.
        """
        if key.team_id is None:
            return None
        rule = self._lookup_one(key, at, use_cache)
        return rule if rule.unit_price is not None else None

    @classmethod
    def invalidate(cls, key: PricingKey) -> None:
        """Drop the cached resolved-rule entry for `key`. Called from signal
        handlers on PricingRule save/delete and from `load_ai_pricing` when
        closing a rule via queryset `.update()` (signals don't fire there).
        """
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
