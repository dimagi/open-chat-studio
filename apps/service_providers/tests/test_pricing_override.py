"""Tests for the override / revert / create-with-pricing HTMX flow on
the LLM provider model list."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.cost_tracking.models import PricingRule, PricingSource, ServiceKind
from apps.service_providers.models import LlmProviderModel
from apps.service_providers.views import _get_models_by_type
from apps.utils.factories.team import TeamWithUsersFactory


def _global_rule(provider, name, kind, price):
    return PricingRule.objects.create(
        team=None,
        provider_type=provider,
        model_name=name,
        service_kind=kind,
        unit_price=price,
        source=PricingSource.SEED,
    )


def _custom_model(team, *, provider="openai", name="test-override-model"):
    return LlmProviderModel.objects.create(team=team, type=provider, name=name, max_token_limit=128000)


def _client_for(team):
    """Session-authenticated client. `TeamWithUsersFactory`'s first member
    is the admin with owner-level group perms (incl. llmprovidermodel CRUD).
    """
    client = Client()
    client.force_login(team.members.first())
    return client


@pytest.mark.django_db()
class TestPricingOverride:
    """POST /pricing/override/submit/ inserts a team-scoped rule per non-empty field."""

    def test_creates_team_scoped_rules_per_field(self):
        team = TeamWithUsersFactory.create()
        model = _custom_model(team, name="test-pa")
        url = reverse("service_providers:pricing_override", kwargs={"team_slug": team.slug, "pk": model.id})

        response = _client_for(team).post(
            url,
            {
                "input_price_per_million_tokens": "5.0",  # $5/M = $0.005/1K
                "output_price_per_million_tokens": "10.0",
            },
        )

        assert response.status_code == 200
        rules = PricingRule.objects.filter(team=team, model_name="test-pa", effective_to__isnull=True)
        kinds = {r.service_kind: r for r in rules}
        assert kinds[ServiceKind.LLM_INPUT].unit_price == Decimal("0.00500000")
        assert kinds[ServiceKind.LLM_OUTPUT].unit_price == Decimal("0.01000000")
        assert kinds[ServiceKind.LLM_INPUT].source == PricingSource.MANUAL

    def test_supersedes_existing_team_rule(self):
        team = TeamWithUsersFactory.create()
        model = _custom_model(team, name="test-pb")
        existing = PricingRule.objects.create(
            team=team,
            provider_type="openai",
            model_name="test-pb",
            service_kind=ServiceKind.LLM_INPUT,
            unit_price="0.00200",
            source=PricingSource.MANUAL,
        )
        url = reverse("service_providers:pricing_override", kwargs={"team_slug": team.slug, "pk": model.id})

        _client_for(team).post(url, {"input_price_per_million_tokens": "7.5"})

        existing.refresh_from_db()
        assert existing.effective_to is not None
        fresh = PricingRule.objects.get(team=team, model_name="test-pb", effective_to__isnull=True)
        assert fresh.unit_price == Decimal("0.00750000")

    def test_empty_form_rejected(self):
        team = TeamWithUsersFactory.create()
        model = _custom_model(team, name="test-pc")
        url = reverse("service_providers:pricing_override", kwargs={"team_slug": team.slug, "pk": model.id})

        response = _client_for(team).post(url, {})

        # 400 with the form re-rendered into the modal body via HX-Retarget so
        # the user sees the validation error in place instead of a raw 400.
        assert response.status_code == 400
        assert response["HX-Retarget"] == "#pricing_override_modal_body"
        assert response["HX-Reswap"] == "innerHTML"
        assert b"Set at least one rate." in response.content

    @pytest.mark.parametrize(
        "price",
        [
            pytest.param("1000000000", id="overflows-unit-price-whole-digits"),
            pytest.param("0.000001", id="more-precision-than-unit-price-holds"),
        ],
    )
    def test_rejects_prices_outside_the_unit_price_domain(self, price):
        """`unit_price` is numeric(14, 8) holding the per-1K rate. Values that do not
        survive the /1000 conversion have to fail form validation: reaching the insert
        would either raise a DataError (500) or silently round the rate to $0."""
        team = TeamWithUsersFactory.create()
        model = _custom_model(team, name="test-pe")
        url = reverse("service_providers:pricing_override", kwargs={"team_slug": team.slug, "pk": model.id})

        response = _client_for(team).post(url, {"input_price_per_million_tokens": price})

        assert response.status_code == 400
        assert not PricingRule.objects.filter(team=team, model_name="test-pe").exists()


@pytest.mark.django_db()
class TestPricingRevert:
    """POST /pricing/revert/ closes every active team rule for the model."""

    def test_closes_team_overrides_only(self):
        team = TeamWithUsersFactory.create()
        model = _custom_model(team, name="test-pd")
        _global_rule("openai", "test-pd", ServiceKind.LLM_INPUT, "0.00250")
        team_rule = PricingRule.objects.create(
            team=team,
            provider_type="openai",
            model_name="test-pd",
            service_kind=ServiceKind.LLM_INPUT,
            unit_price="0.00100",
            source=PricingSource.MANUAL,
        )
        url = reverse("service_providers:pricing_revert", kwargs={"team_slug": team.slug, "pk": model.id})

        response = _client_for(team).post(url)

        assert response.status_code == 200
        team_rule.refresh_from_db()
        assert team_rule.effective_to is not None
        # Global rule untouched.
        global_rule = PricingRule.objects.get(team__isnull=True, model_name="test-pd")
        assert global_rule.effective_to is None


@pytest.mark.django_db()
class TestCreateModelWithPricing:
    """Custom-model creation with optional pricing fields."""

    def test_creates_model_and_rules(self):
        team = TeamWithUsersFactory.create()
        url = reverse("service_providers:llm_provider_model_new", kwargs={"team_slug": team.slug})

        response = _client_for(team).post(
            url,
            {
                "type": "openai",
                "name": "test-create-model",
                "max_token_limit": "128000",
                "input_price_per_million_tokens": "2.5",
                "output_price_per_million_tokens": "10.0",
            },
        )

        assert response.status_code == 200
        assert LlmProviderModel.objects.filter(team=team, name="test-create-model").exists()
        rules = PricingRule.objects.filter(team=team, model_name="test-create-model")
        kinds = {r.service_kind: r.unit_price for r in rules}
        assert kinds[ServiceKind.LLM_INPUT] == Decimal("0.00250000")
        assert kinds[ServiceKind.LLM_OUTPUT] == Decimal("0.01000000")

    def test_rejects_price_that_overflows_unit_price(self):
        """A price too large for `unit_price` must fail validation rather than reach
        the insert, where it would 500 from inside the atomic block."""
        team = TeamWithUsersFactory.create()
        url = reverse("service_providers:llm_provider_model_new", kwargs={"team_slug": team.slug})

        response = _client_for(team).post(
            url,
            {
                "type": "openai",
                "name": "test-create-overflow",
                "max_token_limit": "128000",
                "input_price_per_million_tokens": "1000000000",
            },
        )

        assert response.status_code == 400
        assert not LlmProviderModel.objects.filter(team=team, name="test-create-overflow").exists()


@pytest.mark.django_db()
class TestModelOrdering:
    """`_get_models_by_type` orders each type bucket newest-first with
    deprecated models sunk to the bottom."""

    def _model(self, team, name, *, created, deprecated=False):
        model = LlmProviderModel.objects.create(
            team=team, type="openai", name=name, max_token_limit=128000, deprecated=deprecated
        )
        # created_at is auto_now_add, so override it after the fact.
        LlmProviderModel.objects.filter(pk=model.pk).update(created_at=datetime(2026, 1, created, tzinfo=UTC))
        return model

    def test_orders_newest_first_with_deprecated_last(self):
        team = TeamWithUsersFactory.create()
        newest = self._model(team, "c-newest", created=3)
        oldest = self._model(team, "a-oldest", created=1)
        deprecated_new = self._model(team, "b-deprecated", created=2, deprecated=True)

        ordered = _get_models_by_type(LlmProviderModel.objects.filter(team=team))["openai"]

        assert ordered == [newest, oldest, deprecated_new]
