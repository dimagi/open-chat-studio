from datetime import UTC, datetime

import pytest
from django.http import HttpResponse, HttpResponseGone
from django.test import RequestFactory
from time_machine import travel
from waffle.testutils import override_flag

from apps.experiments.models import Experiment
from apps.utils.factories.team import TeamFactory
from apps.utils.sunset import HttpSunset, due_milestone, is_enforced, register_sunset, sunset_view

SUNSET_AT = datetime(2026, 12, 1, tzinfo=UTC)
BEFORE = datetime(2026, 11, 30, tzinfo=UTC)
AFTER = datetime(2026, 12, 2, tzinfo=UTC)
FLAG = "flag_sunset_test_surface_enforce"


def _spec(**overrides):
    fields = {
        "key": "test_surface",
        "sunset_at": SUNSET_AT,
        "successor_url": "https://example.com/successor/",
        "enforce_flag": FLAG,
        "notification_slug": "test-surface-sunset",
        "notification_title": "Test surface is being removed",
        "build_message": lambda experiments, milestone: f"{len(experiments)} affected ({milestone})",
        "affected_experiments": Experiment.objects.none,
    }
    return HttpSunset(**{**fields, **overrides})


def _request(team):
    request = RequestFactory().get("/")
    request.team = team
    return request


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("now", "flag_on", "expected"),
    [
        pytest.param(BEFORE, False, False, id="before-date-flag-off"),
        pytest.param(AFTER, False, False, id="after-date-flag-off"),
        pytest.param(BEFORE, True, False, id="before-date-flag-on"),
        pytest.param(AFTER, True, True, id="after-date-flag-on"),
    ],
)
def test_gate_needs_the_date_and_the_flag(now, flag_on, expected):
    team = TeamFactory.create()
    with travel(now, tick=False), override_flag(FLAG, active=flag_on):
        assert is_enforced(_spec(), team) is expected


@pytest.mark.django_db()
def test_gate_is_per_team(team_flag):
    gated, other = TeamFactory.create(), TeamFactory.create()
    team_flag(FLAG, gated)
    with travel(AFTER, tick=False):
        assert is_enforced(_spec(), gated) is True
        assert is_enforced(_spec(), other) is False


def test_naive_sunset_date_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        _spec(sunset_at=datetime(2026, 12, 1))


def test_register_sunset_rejects_a_duplicate_key(monkeypatch):
    monkeypatch.setattr("apps.utils.sunset.SUNSETS", {})
    register_sunset(_spec())
    with pytest.raises(ValueError, match="test_surface"):
        register_sunset(_spec())


def test_register_sunset_adds_the_spec(monkeypatch):
    registry = {}
    monkeypatch.setattr("apps.utils.sunset.SUNSETS", registry)
    spec = register_sunset(_spec())
    assert registry == {"test_surface": spec}


def _view():
    def gone(request, *args, **kwargs):
        return HttpResponseGone("gone")

    @sunset_view(_spec(), gone=gone)
    def view(request):
        return HttpResponse("ok")

    return view


@pytest.mark.django_db()
def test_view_runs_with_sunset_headers_while_the_gate_is_closed():
    with travel(AFTER, tick=False), override_flag(FLAG, active=False):
        response = _view()(_request(TeamFactory.create()))
    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"] == "Tue, 01 Dec 2026 00:00:00 GMT"
    assert response.headers["Link"] == '<https://example.com/successor/>; rel="successor-version"'


@pytest.mark.django_db()
def test_view_returns_gone_once_the_gate_is_open():
    with travel(AFTER, tick=False), override_flag(FLAG, active=True):
        response = _view()(_request(TeamFactory.create()))
    assert response.status_code == 410
    assert response.content == b"gone"
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"] == "Tue, 01 Dec 2026 00:00:00 GMT"
    assert response.headers["Link"] == '<https://example.com/successor/>; rel="successor-version"'


@pytest.mark.django_db()
def test_closing_the_gate_restores_the_view():
    team = TeamFactory.create()
    with travel(AFTER, tick=False):
        with override_flag(FLAG, active=True):
            assert _view()(_request(team)).status_code == 410
        with override_flag(FLAG, active=False):
            assert _view()(_request(team)).status_code == 200


def test_sunset_view_keeps_attributes_set_on_the_wrapped_view():
    def inner(request):
        return HttpResponse("ok")

    inner.enforces_team_auth = True
    wrapped = sunset_view(_spec(), gone=lambda request: HttpResponseGone())(inner)
    assert wrapped.enforces_team_auth is True


@pytest.mark.parametrize(
    ("first_detected_at", "now", "expected"),
    [
        pytest.param(
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 10, 20, tzinfo=UTC),
            None,
            id="no-milestone-passed",
        ),
        pytest.param(
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 11, 1, 2, 30, tzinfo=UTC),
            "T-30",
            id="on-t30",
        ),
        pytest.param(
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 11, 10, tzinfo=UTC),
            "T-30",
            id="between-t30-and-t14",
        ),
        pytest.param(
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 11, 25, tzinfo=UTC),
            "T-7",
            id="late-run-after-t7",
        ),
        pytest.param(
            datetime(2026, 11, 11, tzinfo=UTC),
            datetime(2026, 11, 12, tzinfo=UTC),
            None,
            id="t30-passed-before-detection",
        ),
        pytest.param(
            datetime(2026, 11, 11, tzinfo=UTC),
            datetime(2026, 11, 17, 2, 30, tzinfo=UTC),
            "T-14",
            id="t14-after-late-detection",
        ),
        pytest.param(
            datetime(2026, 11, 17, 2, 30, tzinfo=UTC),
            datetime(2026, 11, 18, tzinfo=UTC),
            None,
            id="detected-on-milestone-day",
        ),
        pytest.param(
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 12, 5, tzinfo=UTC),
            "T-1",
            id="after-the-date",
        ),
    ],
)
def test_due_milestone(first_detected_at, now, expected):
    assert due_milestone(_spec(), first_detected_at, now) == expected
