from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cost_tracking.models import PricingRule, ServiceKind
from apps.cost_tracking.services.pricing import UNPRICED, PricingKey, PricingResolver

# One global key shared by every test that doesn't need a different model.
KEY = PricingKey(provider_type="openai", model_name="test-model", service_kind=ServiceKind.LLM_INPUT)


def _make_rule(unit_price: str = "0.001", *, team=None, window: tuple | None = None) -> PricingRule:
    """Create a PricingRule for the test KEY. `window` is an
    (effective_from, effective_to) tuple; None means active-since-yesterday.
    """
    eff_from, eff_to = window if window else (timezone.now() - timedelta(days=1), None)
    return PricingRule.objects.create(
        team=team,
        provider_type=KEY.provider_type,
        model_name=KEY.model_name,
        service_kind=KEY.service_kind,
        unit_price=Decimal(unit_price),
        effective_from=eff_from,
        effective_to=eff_to,
    )


@pytest.mark.django_db()
def test_returns_unpriced_when_no_rule_exists():
    ghost_key = replace(KEY, model_name="ghost-model")
    rule = PricingResolver().resolve(ghost_key, at=timezone.now())
    assert rule is UNPRICED
    assert rule.unit_price is None


@pytest.mark.django_db()
def test_global_rule_is_resolved_when_no_team_override():
    _make_rule(unit_price="0.00015")
    rule = PricingResolver().resolve(KEY, at=timezone.now())
    assert rule.unit_price == Decimal("0.00015")


@pytest.mark.django_db()
def test_team_override_beats_global(team):
    _make_rule(unit_price="0.00015")
    # One-off: team-scoped + non-default source. Easier to inline than to
    # widen the helper's signature for a single call site.
    PricingRule.objects.create(
        team=team,
        provider_type=KEY.provider_type,
        model_name=KEY.model_name,
        service_kind=KEY.service_kind,
        unit_price=Decimal("0.00005"),
        source="manual",
    )
    rule = PricingResolver().resolve(replace(KEY, team_id=team.id), at=timezone.now())
    assert rule.unit_price == Decimal("0.00005")


@pytest.mark.django_db()
def test_falls_back_to_global_when_team_has_no_override(team):
    _make_rule(unit_price="0.00015")
    rule = PricingResolver().resolve(replace(KEY, team_id=team.id), at=timezone.now())
    assert rule.unit_price == Decimal("0.00015")


@pytest.mark.django_db()
def test_expired_rule_is_ignored():
    now = timezone.now()
    _make_rule(unit_price="0.00015", window=(now - timedelta(days=10), now - timedelta(days=5)))
    rule = PricingResolver().resolve(KEY, at=now)
    assert rule is UNPRICED


@pytest.mark.django_db()
def test_time_travel_picks_the_rule_active_at_that_moment():
    now = timezone.now()
    # Old rate: $0.001, in effect days 10-5 ago.
    _make_rule(unit_price="0.00100", window=(now - timedelta(days=10), now - timedelta(days=5)))
    # Current rate: $0.0005, in effect since 5 days ago.
    _make_rule(unit_price="0.00050", window=(now - timedelta(days=5), None))

    historical = PricingResolver().resolve(KEY, at=now - timedelta(days=7), use_cache=False)
    assert historical.unit_price == Decimal("0.00100")

    current = PricingResolver().resolve(KEY, at=now)
    assert current.unit_price == Decimal("0.00050")


@pytest.mark.django_db()
def test_cache_hit_does_not_query_the_db():
    _make_rule(unit_price="0.00015")
    resolver = PricingResolver()

    # First resolve warms the cache.
    resolver.resolve(KEY, at=timezone.now())

    # Second resolve must hit the cache, never touching the DB.
    with patch.object(PricingRule.objects, "filter", side_effect=AssertionError("should not query")) as filter_mock:
        rule = resolver.resolve(KEY, at=timezone.now())
        assert filter_mock.call_count == 0
    assert rule.unit_price == Decimal("0.00015")


@pytest.mark.django_db()
def test_explicit_invalidate_drops_cache_entry():
    _make_rule(unit_price="0.00015")
    resolver = PricingResolver()

    resolver.resolve(KEY, at=timezone.now())
    PricingResolver.invalidate(KEY)

    # Cache cleared -> next resolve goes back to DB.
    with patch.object(PricingRule.objects, "filter", wraps=PricingRule.objects.filter) as filter_mock:
        resolver.resolve(KEY, at=timezone.now())
        assert filter_mock.call_count >= 1
