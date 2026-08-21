from datetime import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.users.models import CustomUser
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory
from apps.utils.factories.user import UserFactory

DATE_RANGE = {"range_type": "custom", "start": "2026-05-01", "end": "2026-05-31"}
INVALID_RANGE = {"range_type": "custom", "start": "not-a-date", "end": "2026-05-31"}
WHEN = timezone.make_aware(datetime(2026, 5, 15, 12, 0))


def _trace(team, **kwargs):
    kwargs.setdefault("at", WHEN)
    return TraceFactory(team=team, **kwargs)


def _teams(response):
    return {team["team_name"]: team for team in response.json()["teams"]}


@pytest.mark.django_db()
def test_non_superuser_blocked(client):
    client.force_login(CustomUser.objects.create(username="staff@acme.com", is_staff=True))
    response = client.get(reverse("ocs_admin:tracing_usage_api"), DATE_RANGE)
    assert response.status_code == 302


@pytest.mark.django_db()
def test_invalid_range_returns_400(superuser_client):
    response = superuser_client.get(reverse("ocs_admin:tracing_usage_api"), INVALID_RANGE)
    assert response.status_code == 400


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("params", "expected_status"),
    [
        pytest.param({"range_type": "custom", "start": "2026-05-01", "end": "2026-05-31"}, 200, id="custom"),
        pytest.param({"range_type": "d30", "start": "2026-05-01", "end": "2026-05-31"}, 200, id="named-range"),
        pytest.param({"range_type": "30d", "start": "2026-05-01", "end": "2026-05-31"}, 400, id="transposed-slug"),
        # `_get_form` only binds the form when all three are present; an unbound form is
        # never valid, so every parameter is required even for a named range that
        # computes its own dates.
        pytest.param({"range_type": "d30"}, 400, id="named-range-without-dates"),
        pytest.param({"start": "2026-05-01", "end": "2026-05-31"}, 400, id="no-range-type"),
        pytest.param({}, 400, id="nothing"),
    ],
)
def test_date_range_params_are_all_required(superuser_client, params, expected_status):
    response = superuser_client.get(reverse("ocs_admin:tracing_usage_api"), params)
    assert response.status_code == expected_status


@pytest.mark.django_db()
def test_reporting_token_grants_access(client, settings):
    settings.PROVIDER_REPORTING_API_TOKEN = "s3cret-token"
    response = client.get(reverse("ocs_admin:tracing_usage_api"), DATE_RANGE, HTTP_AUTHORIZATION="Bearer s3cret-token")
    assert response.status_code == 200


@pytest.mark.django_db()
def test_reports_the_three_counts_separately(superuser_client):
    """Traces, turns and tool calls stay unmixed: how to weight them against a tracing
    invoice is the consumer's decision, not this endpoint's."""
    team = TeamFactory(name="Alpha")
    _trace(team, n_turns=3, n_toolcalls=2)
    _trace(team, n_turns=1, n_toolcalls=0)

    response = superuser_client.get(reverse("ocs_admin:tracing_usage_api"), DATE_RANGE)

    assert response.status_code == 200
    alpha = _teams(response)["Alpha"]
    assert alpha["traces"] == 2
    assert alpha["turns"] == 4
    assert alpha["toolcalls"] == 2
    assert "units" not in alpha


@pytest.mark.django_db()
def test_includes_team_creator(superuser_client):
    creator = UserFactory(username="creator", email="creator@example.com")
    team = TeamFactory(name="Alpha", created_by=creator)
    _trace(team)

    alpha = _teams(superuser_client.get(reverse("ocs_admin:tracing_usage_api"), DATE_RANGE))["Alpha"]

    assert alpha["created_by"] == {
        "id": creator.id,
        "username": "creator",
        "email": "creator@example.com",
    }


@pytest.mark.django_db()
def test_null_turn_counts_report_as_zero(superuser_client):
    """`n_turns`/`n_toolcalls` are nullable; the sums must coalesce so a consumer can
    add them without null-checking every field."""
    team = TeamFactory(name="Alpha")
    _trace(team, n_turns=None, n_toolcalls=None)
    _trace(team, n_turns=2, n_toolcalls=None)

    response = superuser_client.get(reverse("ocs_admin:tracing_usage_api"), DATE_RANGE)

    alpha = _teams(response)["Alpha"]
    assert alpha["traces"] == 2
    assert alpha["turns"] == 2
    assert alpha["toolcalls"] == 0


@pytest.mark.django_db()
def test_teams_ordered_by_trace_count(superuser_client):
    small = TeamFactory(name="Small")
    big = TeamFactory(name="Big")
    _trace(small, n_turns=99)
    for _ in range(3):
        _trace(big, n_turns=1)

    response = superuser_client.get(reverse("ocs_admin:tracing_usage_api"), DATE_RANGE)

    assert [team["team_name"] for team in response.json()["teams"]] == ["Big", "Small"]


@pytest.mark.django_db()
def test_excludes_traces_outside_the_range(superuser_client):
    team = TeamFactory(name="Alpha")
    _trace(team, n_turns=1)
    _trace(team, n_turns=99, at=timezone.make_aware(datetime(2026, 4, 30, 23, 59)))

    alpha = _teams(superuser_client.get(reverse("ocs_admin:tracing_usage_api"), DATE_RANGE))["Alpha"]

    assert alpha["traces"] == 1
    assert alpha["turns"] == 1


@pytest.mark.django_db()
def test_teamless_traces_are_skipped(superuser_client):
    """`Trace.team` is SET_NULL, so a deleted team leaves orphan rows with nothing to
    apportion cost to."""
    _trace(None, n_turns=5)
    _trace(TeamFactory(name="Alpha"), n_turns=1)

    response = superuser_client.get(reverse("ocs_admin:tracing_usage_api"), DATE_RANGE)

    assert list(_teams(response)) == ["Alpha"]
