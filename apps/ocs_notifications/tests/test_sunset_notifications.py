from datetime import UTC, datetime

import pytest
from time_machine import travel
from waffle.testutils import override_flag

from apps.experiments.models import Experiment
from apps.ocs_notifications.models import EventUser, NotificationEvent
from apps.ocs_notifications.tasks import notify_http_sunsets
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory, TeamWithUsersFactory
from apps.utils.sunset import HttpSunset

SUNSET_AT = datetime(2026, 12, 1, tzinfo=UTC)
FLAG = "flag_sunset_test_surface_enforce"
PERMISSION = "bot_channels.add_experimentchannel"


def _register(monkeypatch, experiment_ids):
    spec = HttpSunset(
        key="test_surface",
        sunset_at=SUNSET_AT,
        successor_url="https://example.com/successor/",
        enforce_flag=FLAG,
        notification_slug="test-surface-sunset",
        notification_title="Test surface is being removed",
        build_message=lambda experiments, milestone: f"{len(experiments)} affected ({milestone})",
        affected_experiments=lambda: Experiment.objects.filter(id__in=experiment_ids),
    )
    monkeypatch.setattr("apps.ocs_notifications.sunsets.SUNSETS", {"test_surface": spec})
    return spec


def _milestones(team):
    return sorted(
        EventUser.objects.filter(team=team).values_list("event_type__event_data__milestone", flat=True).distinct()
    )


def _run_at(moment):
    with travel(moment, tick=False):
        notify_http_sunsets()


@pytest.mark.django_db()
def test_first_run_after_a_milestone_sends_only_first_detection(monkeypatch):
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    _register(monkeypatch, [experiment.id])

    _run_at(datetime(2026, 11, 11, 2, 30, tzinfo=UTC))

    assert _milestones(experiment.team) == ["first-detection"]
    assert NotificationEvent.objects.filter(team=experiment.team).count() == 1


@pytest.mark.django_db()
def test_each_later_milestone_opens_its_own_thread(monkeypatch):
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    _register(monkeypatch, [experiment.id])

    _run_at(datetime(2026, 11, 11, 2, 30, tzinfo=UTC))
    _run_at(datetime(2026, 11, 17, 2, 30, tzinfo=UTC))
    _run_at(datetime(2026, 11, 24, 2, 30, tzinfo=UTC))

    assert _milestones(experiment.team) == ["T-14", "T-7", "first-detection"]


@pytest.mark.django_db()
def test_a_missed_run_sends_the_milestone_on_the_next_run(monkeypatch):
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    _register(monkeypatch, [experiment.id])

    _run_at(datetime(2026, 11, 11, 2, 30, tzinfo=UTC))
    _run_at(datetime(2026, 11, 25, 2, 30, tzinfo=UTC))

    assert _milestones(experiment.team) == ["T-7", "first-detection"]


@pytest.mark.django_db()
def test_a_second_run_the_same_day_sends_nothing_new(monkeypatch):
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    _register(monkeypatch, [experiment.id])

    _run_at(datetime(2026, 11, 17, 2, 30, tzinfo=UTC))
    _run_at(datetime(2026, 11, 17, 9, 0, tzinfo=UTC))

    assert NotificationEvent.objects.filter(team=experiment.team).count() == 1


@pytest.mark.django_db()
def test_only_permission_holders_are_notified(monkeypatch):
    team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)
    _register(monkeypatch, [experiment.id])

    _run_at(datetime(2026, 11, 11, 2, 30, tzinfo=UTC))

    expected = {m.user_id for m in team.membership_set.all() if m.has_perms([PERMISSION])}
    assert expected
    assert set(EventUser.objects.filter(team=team).values_list("user_id", flat=True)) == expected


@pytest.mark.django_db()
def test_no_recipients_does_not_create_an_event(monkeypatch):
    experiment = ExperimentFactory.create(team=TeamFactory.create())
    _register(monkeypatch, [experiment.id])

    _run_at(datetime(2026, 11, 11, 2, 30, tzinfo=UTC))
    _run_at(datetime(2026, 11, 12, 2, 30, tzinfo=UTC))

    assert not NotificationEvent.objects.filter(team=experiment.team).exists()


@pytest.mark.django_db()
def test_a_team_with_no_affected_chatbots_gets_nothing(monkeypatch):
    team = TeamWithUsersFactory.create()
    _register(monkeypatch, [])

    _run_at(datetime(2026, 11, 11, 2, 30, tzinfo=UTC))

    assert not NotificationEvent.objects.filter(team=team).exists()


@pytest.mark.django_db()
def test_an_enforced_sunset_sends_nothing(monkeypatch):
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    _register(monkeypatch, [experiment.id])

    with override_flag(FLAG, active=True):
        _run_at(datetime(2026, 12, 2, 2, 30, tzinfo=UTC))

    assert not NotificationEvent.objects.filter(team=experiment.team).exists()


@pytest.mark.django_db()
def test_the_notice_links_each_chatbot_and_the_successor(monkeypatch):
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create(), name="Clinic bot")
    _register(monkeypatch, [experiment.id])

    _run_at(datetime(2026, 11, 11, 2, 30, tzinfo=UTC))

    event = NotificationEvent.objects.get(team=experiment.team)
    assert event.title == "Test surface is being removed"
    assert event.message == "1 affected (first-detection)"
    assert event.links == {
        "Clinic bot": experiment.get_absolute_url(),
        "Migration guide": "https://example.com/successor/",
    }
