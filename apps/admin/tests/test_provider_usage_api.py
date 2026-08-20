from datetime import datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.cost_tracking.models import ServiceKind, UsageSource
from apps.users.models import CustomUser
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.team import TeamFactory

DATE_RANGE = {"range_type": "custom", "start": "2026-05-01", "end": "2026-05-31"}
INVALID_RANGE = {"range_type": "custom", "start": "not-a-date", "end": "2026-05-31"}
WHEN = timezone.make_aware(datetime(2026, 5, 15, 12, 0))


def _usage(team, **kwargs):
    kwargs.setdefault("at", WHEN)
    return UsageRecordFactory(team=team, **kwargs)


@pytest.mark.django_db()
def test_non_superuser_blocked(client):
    client.force_login(CustomUser.objects.create(username="staff@acme.com", is_staff=True))
    response = client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE)
    assert response.status_code == 302


@pytest.mark.django_db()
def test_invalid_range_returns_400(superuser_client):
    response = superuser_client.get(reverse("ocs_admin:provider_usage_api"), INVALID_RANGE)
    assert response.status_code == 400


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("configured_token", "auth_header", "expected_status"),
    [
        pytest.param("s3cret-token", "Bearer s3cret-token", 200, id="valid-token-grants-access"),
        pytest.param("s3cret-token", "Bearer wrong", 302, id="invalid-token-falls-back-to-session"),
        pytest.param(None, "Bearer anything", 302, id="token-ignored-when-unset"),
        pytest.param("s3cret-token", "Bearer nön-ascii", 302, id="non-ascii-header-rejected"),
    ],
)
def test_reporting_token_auth(client, settings, configured_token, auth_header, expected_status):
    settings.PROVIDER_REPORTING_API_TOKEN = configured_token
    response = client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE, HTTP_AUTHORIZATION=auth_header)
    assert response.status_code == expected_status


@pytest.mark.django_db()
def test_token_totals_reconcile_with_per_model_detail(superuser_client):
    """The whole point of reading one table: `total_tokens` is exactly the sum of
    `models[].tokens`, and `total_cost` the sum of their costs."""
    team_a = TeamFactory(name="Alpha")
    team_b = TeamFactory(name="Bravo")
    _usage(team_a, provider_type="openai", model_name="gpt-4o", quantity=500, cost=Decimal("1.25"))
    _usage(team_a, provider_type="anthropic", model_name="claude", quantity=300, cost=Decimal("0.75"))
    _usage(team_b, provider_type="openai", model_name="gpt-4o", quantity=100, cost=Decimal("0.10"))

    response = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE)

    assert response.status_code == 200
    teams = {t["team_name"]: t for t in response.json()["teams"]}

    alpha = teams["Alpha"]
    assert alpha["total_tokens"] == 800
    assert alpha["team_slug"] == team_a.slug
    assert Decimal(alpha["total_cost"]["USD"]) == Decimal("2.00")
    models = {m["model_name"]: m for m in alpha["models"]}
    assert Decimal(models["gpt-4o"]["cost"]) == Decimal("1.25")
    assert models["gpt-4o"]["tokens"] == 500
    assert alpha["total_tokens"] == sum(m["tokens"] for m in alpha["models"])

    assert teams["Bravo"]["total_tokens"] == 100


@pytest.mark.django_db()
def test_teams_ordered_by_token_total(superuser_client):
    _usage(TeamFactory(name="Small"), quantity=10)
    _usage(TeamFactory(name="Big"), quantity=900)

    payload = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE).json()

    assert [t["team_name"] for t in payload["teams"]] == ["Big", "Small"]


@pytest.mark.django_db()
def test_includes_team_metadata(superuser_client, settings):
    settings.TEAM_METADATA_FIELDS = [
        {"key": "team_owner", "label": "Team Owner"},
        {"key": "region", "label": "Region"},
    ]
    team = TeamFactory(name="Alpha", metadata={"team_owner": "Jia", "internal_only": "hidden"})
    _usage(team, quantity=100)

    payload = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE).json()

    assert payload["metadata_fields"] == [
        {"key": "team_owner", "label": "Team Owner", "type": "text"},
        {"key": "region", "label": "Region", "type": "text"},
    ]
    alpha = {t["team_name"]: t for t in payload["teams"]}["Alpha"]
    # Only configured fields are exposed; unconfigured keys stay hidden, missing ones blank.
    assert alpha["metadata"] == {"team_owner": "Jia", "region": ""}


@pytest.mark.django_db()
def test_counts_untraced_evaluation_spend_in_both_halves(superuser_client):
    """Billing view: eval spend is the team's spend, counted with no per-source split
    (ADR-0048). Judge calls have no trace, so a trace-sourced token count saw the cost
    but not the tokens — reading UsageRecord for both closes that."""
    team = TeamFactory(name="Alpha")
    _usage(team, model_name="gpt-4o", quantity=1000, cost=Decimal("1.00"))
    _usage(team, model_name="gpt-4o", quantity=250, cost=Decimal("0.25"), source=UsageSource.EVALUATION, trace=None)

    payload = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE).json()

    alpha = {t["team_name"]: t for t in payload["teams"]}["Alpha"]
    assert alpha["total_tokens"] == 1250
    assert Decimal(alpha["total_cost"]["USD"]) == Decimal("1.25")


@pytest.mark.django_db()
def test_total_tokens_spans_service_kinds(superuser_client):
    """One LLM call becomes several per-kind rows; the team total is every kind, so it
    tracks the trace-sourced `usage_metadata` total it replaces."""
    team = TeamFactory(name="Alpha")
    _usage(team, model_name="gpt-4o", service_kind=ServiceKind.LLM_INPUT, quantity=700)
    _usage(team, model_name="gpt-4o", service_kind=ServiceKind.LLM_CACHED_INPUT, quantity=200)
    _usage(team, model_name="gpt-4o", service_kind=ServiceKind.LLM_CACHE_WRITE, quantity=100)
    _usage(team, model_name="gpt-4o", service_kind=ServiceKind.LLM_OUTPUT, quantity=50)

    payload = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE).json()

    alpha = {t["team_name"]: t for t in payload["teams"]}["Alpha"]
    assert alpha["total_tokens"] == 1050


@pytest.mark.django_db()
def test_total_cost_keeps_currencies_separate(superuser_client):
    team = TeamFactory(name="Alpha")
    _usage(team, model_name="gpt-4o", cost=Decimal("1.25"), currency="USD")
    _usage(team, model_name="claude", cost=Decimal("0.90"), currency="EUR")

    response = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE)

    alpha = {t["team_name"]: t for t in response.json()["teams"]}["Alpha"]
    # Mixed currencies are never summed into one meaningless scalar.
    assert Decimal(alpha["total_cost"]["USD"]) == Decimal("1.25")
    assert Decimal(alpha["total_cost"]["EUR"]) == Decimal("0.90")


@pytest.mark.django_db()
def test_excludes_records_outside_the_range(superuser_client):
    team = TeamFactory(name="Alpha")
    _usage(team, quantity=500)
    _usage(team, quantity=999, at=timezone.make_aware(datetime(2026, 4, 30, 23, 59)))

    response = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE)

    alpha = {t["team_name"]: t for t in response.json()["teams"]}["Alpha"]
    assert alpha["total_tokens"] == 500
