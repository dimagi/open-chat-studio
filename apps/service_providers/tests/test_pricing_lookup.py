"""Tests for `_pricing_lookup` — the bulk pricing-by-model helper that
powers the cost columns on the LLM provider form."""

from decimal import Decimal

import pytest

from apps.cost_tracking.models import PricingRule, PricingSource, ServiceKind
from apps.service_providers.models import LlmProviderModel
from apps.service_providers.views import _pricing_lookup
from apps.utils.factories.team import TeamFactory


def _model(team, *, provider="openai", name="gpt-4o-mini", token_limit=128000):
    return LlmProviderModel.objects.create(team=team, type=provider, name=name, max_token_limit=token_limit)


def _rule(team, *, provider, name, kind, price, source=PricingSource.SEED):
    return PricingRule.objects.create(
        team=team,
        provider_type=provider,
        model_name=name,
        service_kind=kind,
        unit_price=price,
        source=source,
    )


@pytest.mark.django_db()
class TestPricingLookup:
    def test_empty_input_returns_empty_dict(self):
        team = TeamFactory.create()
        assert _pricing_lookup(team, []) == {}

    def test_returns_global_rule_when_no_override(self):
        team = TeamFactory.create()
        model = _model(team, name="test-model-a")
        _rule(team=None, provider="openai", name="test-model-a", kind=ServiceKind.LLM_INPUT, price="0.00250")

        result = _pricing_lookup(team, [model])

        assert result[model.id][ServiceKind.LLM_INPUT.value]["unit_price"] == Decimal("0.00250000")
        assert result[model.id][ServiceKind.LLM_INPUT.value]["scope"] == "global"

    def test_team_override_wins_over_global(self):
        team = TeamFactory.create()
        model = _model(team, name="test-model-b")
        _rule(team=None, provider="openai", name="test-model-b", kind=ServiceKind.LLM_INPUT, price="0.00250")
        _rule(
            team=team,
            provider="openai",
            name="test-model-b",
            kind=ServiceKind.LLM_INPUT,
            price="0.00100",
            source=PricingSource.MANUAL,
        )

        result = _pricing_lookup(team, [model])

        rate = result[model.id][ServiceKind.LLM_INPUT.value]
        assert rate["unit_price"] == Decimal("0.00100000")
        assert rate["scope"] == "team"
        assert rate["source"] == PricingSource.MANUAL

    def test_unpriced_model_absent_from_result(self):
        team = TeamFactory.create()
        priced = _model(team, name="test-model-c")
        unpriced = _model(team, name="test-model-d")
        _rule(team=None, provider="openai", name="test-model-c", kind=ServiceKind.LLM_INPUT, price="0.00250")

        result = _pricing_lookup(team, [priced, unpriced])

        assert priced.id in result
        assert unpriced.id not in result

    def test_multiple_service_kinds_per_model(self):
        team = TeamFactory.create()
        model = _model(team, name="test-model-e")
        _rule(team=None, provider="openai", name="test-model-e", kind=ServiceKind.LLM_INPUT, price="0.00250")
        _rule(team=None, provider="openai", name="test-model-e", kind=ServiceKind.LLM_OUTPUT, price="0.01000")
        _rule(team=None, provider="openai", name="test-model-e", kind=ServiceKind.LLM_CACHED_INPUT, price="0.00125")

        result = _pricing_lookup(team, [model])

        # `primary` / `has_team_override` synthetic keys are part of the shape
        # but only the service-kind rates carry pricing data.
        expected_kinds = {
            ServiceKind.LLM_INPUT.value,
            ServiceKind.LLM_OUTPUT.value,
            ServiceKind.LLM_CACHED_INPUT.value,
        }
        assert expected_kinds <= set(result[model.id].keys())

    def test_closed_rules_excluded(self):
        team = TeamFactory.create()
        model = _model(team, name="test-model-f")
        rule = _rule(team=None, provider="openai", name="test-model-f", kind=ServiceKind.LLM_INPUT, price="0.00250")
        rule.effective_to = rule.effective_from
        rule.save()

        result = _pricing_lookup(team, [model])

        assert model.id not in result

    def test_other_team_rules_not_visible(self):
        team = TeamFactory.create()
        other = TeamFactory.create()
        model = _model(team, name="test-model-g")
        _rule(
            team=other,
            provider="openai",
            name="test-model-g",
            kind=ServiceKind.LLM_INPUT,
            price="0.00999",
        )

        result = _pricing_lookup(team, [model])

        assert model.id not in result
