from datetime import datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.trace.models import Trace, TraceStatus
from apps.users.models import CustomUser
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory

DATE_RANGE = {"range_type": "custom", "start": "2026-05-01", "end": "2026-05-31"}
INVALID_RANGE = {"range_type": "custom", "start": "not-a-date", "end": "2026-05-31"}
WHEN = timezone.make_aware(datetime(2026, 5, 15, 12, 0))


@pytest.fixture()
def superuser_client(client):
    user = CustomUser.objects.create(username="admin@acme.com", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


def _trace(team, tokens):
    trace = TraceFactory(team=team, status=TraceStatus.SUCCESS, n_total_tokens=tokens)
    # timestamp uses auto_now_add, so place it inside the window after creation.
    Trace.objects.filter(pk=trace.pk).update(timestamp=WHEN)
    return trace


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
def test_valid_reporting_token_grants_access(client, settings):
    settings.PROVIDER_REPORTING_API_TOKEN = "s3cret-token"
    response = client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE, HTTP_AUTHORIZATION="Bearer s3cret-token")
    assert response.status_code == 200


@pytest.mark.django_db()
def test_invalid_reporting_token_falls_back_to_session(client, settings):
    settings.PROVIDER_REPORTING_API_TOKEN = "s3cret-token"
    response = client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE, HTTP_AUTHORIZATION="Bearer wrong")
    assert response.status_code == 302  # no valid token, no superuser session


@pytest.mark.django_db()
def test_reporting_token_ignored_when_unset(client, settings):
    settings.PROVIDER_REPORTING_API_TOKEN = None
    response = client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE, HTTP_AUTHORIZATION="Bearer anything")
    assert response.status_code == 302


@pytest.mark.django_db()
def test_merges_token_totals_and_cost_detail(superuser_client):
    team_a = TeamFactory(name="Alpha")
    team_b = TeamFactory(name="Bravo")
    _trace(team_a, 500)
    _trace(team_a, 300)
    _trace(team_b, 100)
    UsageRecordFactory(
        team=team_a, provider_type="openai", model_name="gpt-4o", quantity=500, cost=Decimal("1.25"), at=WHEN
    )
    UsageRecordFactory(
        team=team_a, provider_type="anthropic", model_name="claude", quantity=200, cost=Decimal("0.75"), at=WHEN
    )

    response = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE)

    assert response.status_code == 200
    teams = {t["team_name"]: t for t in response.json()["teams"]}

    alpha = teams["Alpha"]
    assert alpha["run_count"] == 2
    assert alpha["total_tokens"] == 800
    assert alpha["team_slug"] == team_a.slug
    assert Decimal(alpha["total_cost"]["USD"]) == Decimal("2.00")
    models = {m["model_name"]: m for m in alpha["models"]}
    assert Decimal(models["gpt-4o"]["cost"]) == Decimal("1.25")
    assert models["gpt-4o"]["tokens"] == 500

    bravo = teams["Bravo"]
    assert bravo["run_count"] == 1
    assert bravo["models"] == []
    assert bravo["total_cost"] == {}


@pytest.mark.django_db()
def test_total_cost_keeps_currencies_separate(superuser_client):
    team = TeamFactory(name="Alpha")
    _trace(team, 100)
    UsageRecordFactory(team=team, model_name="gpt-4o", cost=Decimal("1.25"), currency="USD", at=WHEN)
    UsageRecordFactory(team=team, model_name="claude", cost=Decimal("0.90"), currency="EUR", at=WHEN)

    response = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE)

    alpha = {t["team_name"]: t for t in response.json()["teams"]}["Alpha"]
    # Mixed currencies are never summed into one meaningless scalar.
    assert Decimal(alpha["total_cost"]["USD"]) == Decimal("1.25")
    assert Decimal(alpha["total_cost"]["EUR"]) == Decimal("0.90")


@pytest.mark.django_db()
def test_excludes_pending_traces(superuser_client):
    team = TeamFactory(name="Alpha")
    _trace(team, 500)
    pending = TraceFactory(team=team, status=TraceStatus.PENDING, n_total_tokens=999)
    Trace.objects.filter(pk=pending.pk).update(timestamp=WHEN)

    response = superuser_client.get(reverse("ocs_admin:provider_usage_api"), DATE_RANGE)

    alpha = {t["team_name"]: t for t in response.json()["teams"]}["Alpha"]
    assert alpha["run_count"] == 1
    assert alpha["total_tokens"] == 500
