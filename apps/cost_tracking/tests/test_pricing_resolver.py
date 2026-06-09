from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cost_tracking.models import PricingRule, ServiceKind
from apps.cost_tracking.services.pricing import UNPRICED, PricingResolver


def _make_rule(team=None, *, unit_price="0.001", effective_from=None, effective_to=None, **kwargs):
    return PricingRule.objects.create(
        team=team,
        provider_type=kwargs.pop("provider_type", "openai"),
        model_name=kwargs.pop("model_name", "test-model"),
        service_kind=kwargs.pop("service_kind", ServiceKind.LLM_INPUT),
        unit_price=Decimal(unit_price),
        effective_from=effective_from or timezone.now() - timedelta(days=1),
        effective_to=effective_to,
        **kwargs,
    )


@pytest.mark.django_db()
def test_returns_unpriced_when_no_rule_exists():
    rule = PricingResolver().resolve(
        team_id=None,
        provider_type="openai",
        model_name="ghost-model",
        service_kind=ServiceKind.LLM_INPUT,
        at=timezone.now(),
    )
    assert rule is UNPRICED
    assert rule.unit_price is None


@pytest.mark.django_db()
def test_global_rule_is_resolved_when_no_team_override():
    _make_rule(team=None, unit_price="0.00015")
    rule = PricingResolver().resolve(
        team_id=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        at=timezone.now(),
    )
    assert rule.unit_price == Decimal("0.00015")


@pytest.mark.django_db()
def test_team_override_beats_global(team):
    _make_rule(team=None, unit_price="0.00015")
    _make_rule(team=team, unit_price="0.00005", source="manual")
    rule = PricingResolver().resolve(
        team_id=team.id,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        at=timezone.now(),
    )
    assert rule.unit_price == Decimal("0.00005")


@pytest.mark.django_db()
def test_falls_back_to_global_when_team_has_no_override(team):
    _make_rule(team=None, unit_price="0.00015")
    rule = PricingResolver().resolve(
        team_id=team.id,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        at=timezone.now(),
    )
    assert rule.unit_price == Decimal("0.00015")


@pytest.mark.django_db()
def test_expired_rule_is_ignored():
    now = timezone.now()
    _make_rule(
        team=None,
        unit_price="0.00015",
        effective_from=now - timedelta(days=10),
        effective_to=now - timedelta(days=5),
    )
    rule = PricingResolver().resolve(
        team_id=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        at=now,
    )
    assert rule is UNPRICED


@pytest.mark.django_db()
def test_time_travel_picks_the_rule_active_at_that_moment():
    now = timezone.now()
    # Old rate: $0.001, in effect days 10-5 ago.
    _make_rule(
        team=None,
        unit_price="0.00100",
        effective_from=now - timedelta(days=10),
        effective_to=now - timedelta(days=5),
    )
    # Current rate: $0.0005, in effect since 5 days ago.
    _make_rule(
        team=None,
        unit_price="0.00050",
        effective_from=now - timedelta(days=5),
    )

    # Resolved 7 days ago -> should see the old rate.
    historical = PricingResolver().resolve(
        team_id=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        at=now - timedelta(days=7),
        use_cache=False,
    )
    assert historical.unit_price == Decimal("0.00100")

    # Resolved now -> current rate.
    current = PricingResolver().resolve(
        team_id=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        at=now,
    )
    assert current.unit_price == Decimal("0.00050")


@pytest.mark.django_db()
def test_cache_hit_does_not_query_the_db():
    _make_rule(team=None, unit_price="0.00015")
    resolver = PricingResolver()

    # First resolve warms the cache.
    resolver.resolve(
        team_id=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        at=timezone.now(),
    )

    # Second resolve must hit the cache, never touching the DB.
    with patch.object(PricingRule.objects, "filter", side_effect=AssertionError("should not query")) as filter_mock:
        rule = resolver.resolve(
            team_id=None,
            provider_type="openai",
            model_name="test-model",
            service_kind=ServiceKind.LLM_INPUT,
            at=timezone.now(),
        )
        assert filter_mock.call_count == 0
    assert rule.unit_price == Decimal("0.00015")


@pytest.mark.django_db()
def test_explicit_invalidate_drops_cache_entry():
    _make_rule(team=None, unit_price="0.00015")
    resolver = PricingResolver()

    resolver.resolve(
        team_id=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
        at=timezone.now(),
    )

    PricingResolver.invalidate(
        team_id=None,
        provider_type="openai",
        model_name="test-model",
        service_kind=ServiceKind.LLM_INPUT,
    )

    # Cache cleared -> next resolve goes back to DB. Mock confirms it does.
    with patch.object(PricingRule.objects, "filter", wraps=PricingRule.objects.filter) as filter_mock:
        resolver.resolve(
            team_id=None,
            provider_type="openai",
            model_name="test-model",
            service_kind=ServiceKind.LLM_INPUT,
            at=timezone.now(),
        )
        assert filter_mock.call_count >= 1
