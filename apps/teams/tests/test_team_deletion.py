from datetime import timedelta

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.request import Request

from apps.api.models import UserAPIKey
from apps.api.permissions import ApiKeyAuthentication
from apps.evaluations.models import EvaluationMessage
from apps.events.models import EventAction, StaticTrigger
from apps.teams.deletion import (
    TeamDeletionError,
    cancel_team_deletion,
    delete_team_if_due,
    get_teams_due_for_deletion,
    request_team_deletion,
)
from apps.teams.export_service import frozen_experiment_q
from apps.teams.helpers import get_default_team_for_user
from apps.teams.models import Team
from apps.trace.models import Trace
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.evaluations import EvaluationDatasetFactory, EvaluationMessageFactory
from apps.utils.factories.events import ScheduledTriggerFactory, StaticTriggerFactory, TimeoutTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.factories.traces import TraceFactory
from apps.utils.factories.user import UserFactory


def _admin(team):
    return next(m.user for m in team.membership_set.all() if m.is_team_admin())


def _member(team):
    return next(m.user for m in team.membership_set.all() if not m.is_team_admin())


def _schedule(team, days_ago=0):
    request_team_deletion(team, _admin(team))
    Team.objects.filter(id=team.id).update(deletion_requested_at=timezone.now() - timedelta(days=days_ago))
    team.refresh_from_db()
    return team


@pytest.fixture()
def pending_team(db):
    return _schedule(TeamWithUsersFactory.create())


@pytest.mark.django_db()
def test_request_team_deletion_disables_team_and_emails_requester(mailoutbox, django_capture_on_commit_callbacks):
    team = TeamWithUsersFactory.create()
    admin = _admin(team)

    with django_capture_on_commit_callbacks(execute=True):
        request_team_deletion(team, admin, "self")

    team.refresh_from_db()
    assert team.is_pending_deletion
    assert team.deletion_requested_by == admin
    assert team.deletion_due_at == team.deletion_requested_at + settings.TEAM_DELETION_GRACE_PERIOD
    assert [m.to for m in mailoutbox] == [[admin.email]]
    assert "scheduled for deletion" in mailoutbox[0].subject


def test_request_team_deletion_twice_is_refused(pending_team):
    with pytest.raises(TeamDeletionError):
        request_team_deletion(pending_team, _admin(pending_team))


def test_cancel_team_deletion_restores_team(pending_team):
    cancel_team_deletion(pending_team)
    pending_team.refresh_from_db()
    assert not pending_team.is_pending_deletion
    assert pending_team.deletion_requested_by is None


@pytest.mark.parametrize(
    ("days_ago", "due"),
    [
        pytest.param(0, False, id="just-requested"),
        pytest.param(settings.TEAM_DELETION_GRACE_PERIOD.days - 1, False, id="inside-grace-period"),
        pytest.param(settings.TEAM_DELETION_GRACE_PERIOD.days, True, id="at-grace-period-boundary"),
        pytest.param(settings.TEAM_DELETION_GRACE_PERIOD.days + 1, True, id="past-grace-period"),
    ],
)
@pytest.mark.django_db()
def test_teams_due_for_deletion(days_ago, due):
    team = _schedule(TeamWithUsersFactory.create(), days_ago=days_ago)
    TeamWithUsersFactory.create()  # not scheduled

    assert list(get_teams_due_for_deletion()) == ([team] if due else [])
    assert delete_team_if_due(team.id) is due
    assert Team.objects.filter(id=team.id).exists() is not due


@pytest.mark.django_db()
def test_cancelled_team_is_not_deleted():
    team = _schedule(TeamWithUsersFactory.create(), days_ago=settings.TEAM_DELETION_GRACE_PERIOD.days + 1)
    cancel_team_deletion(team)

    assert delete_team_if_due(team.id) is False
    assert Team.objects.filter(id=team.id).exists()


@pytest.mark.django_db()
def test_hard_delete_removes_records_the_team_cascade_misses(mailoutbox, django_capture_on_commit_callbacks):
    team = TeamWithUsersFactory.create()
    other_team = TeamWithUsersFactory.create()
    experiment = ExperimentFactory.create(team=team)

    trace = TraceFactory.create(team=team, experiment=experiment)
    other_trace = TraceFactory.create(team=other_team)
    evaluation_message = EvaluationMessageFactory.create()
    EvaluationDatasetFactory.create(team=team, messages=[evaluation_message])
    other_evaluation_message = EvaluationMessageFactory.create()
    EvaluationDatasetFactory.create(team=other_team, messages=[other_evaluation_message])
    action_ids = [
        StaticTriggerFactory.create(experiment=experiment).action_id,
        TimeoutTriggerFactory.create(experiment=experiment).action_id,
        ScheduledTriggerFactory.create(experiment=experiment).action_id,
    ]
    other_action_id = StaticTriggerFactory.create(experiment=ExperimentFactory.create(team=other_team)).action_id

    _schedule(team, days_ago=settings.TEAM_DELETION_GRACE_PERIOD.days + 1)
    mailoutbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        assert delete_team_if_due(team.id)

    assert not Team.objects.filter(id=team.id).exists()
    assert not Trace.objects.filter(id=trace.id).exists()
    assert Trace.objects.filter(id=other_trace.id).exists()
    assert not EvaluationMessage.objects.filter(id=evaluation_message.id).exists()
    assert EvaluationMessage.objects.filter(id=other_evaluation_message.id).exists()
    assert not EventAction.objects.filter(id__in=action_ids).exists()
    assert EventAction.objects.filter(id=other_action_id).exists()
    assert StaticTrigger.objects.filter(action_id=other_action_id).exists()
    assert len(mailoutbox) == 1
    assert "has been deleted" in mailoutbox[0].subject


@pytest.mark.parametrize(
    ("recipients", "scheduled"),
    [
        pytest.param("self", True, id="valid-recipients"),
        pytest.param("everyone", False, id="invalid-recipients"),
    ],
)
@pytest.mark.django_db()
def test_delete_view_schedules_deletion(client, recipients, scheduled):
    team = TeamWithUsersFactory.create()
    client.force_login(_admin(team))

    response = client.post(
        reverse("single_team:delete_team", args=[team.slug]), {"notification_recipients": recipients}
    )

    assert response.status_code == 302
    expected_url = "teams:pending_deletion" if scheduled else "single_team:manage_team"
    assert response.url == reverse(expected_url, args=[team.slug])
    team.refresh_from_db()
    assert team.is_pending_deletion is scheduled


def test_team_pages_redirect_to_pending_page(client, pending_team):
    client.force_login(_member(pending_team))
    response = client.get(pending_team.dashboard_url)
    assert response.status_code == 302
    assert response.url == reverse("teams:pending_deletion", args=[pending_team.slug])


def test_team_pages_are_not_found_for_anonymous_users(client, pending_team):
    response = client.get(pending_team.dashboard_url)
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("who", "status", "can_cancel"),
    [
        pytest.param("admin", 200, True, id="admin"),
        pytest.param("member", 200, False, id="member"),
        pytest.param("outsider", 404, None, id="outsider"),
        pytest.param("superuser", 200, True, id="superuser"),
    ],
)
def test_pending_deletion_page(client, pending_team, who, status, can_cancel):
    user = {
        "admin": lambda: _admin(pending_team),
        "member": lambda: _member(pending_team),
        "outsider": lambda: UserFactory.create(),
        "superuser": lambda: UserFactory.create(is_superuser=True),
    }[who]()
    client.force_login(user)

    response = client.get(reverse("teams:pending_deletion", args=[pending_team.slug]))

    assert response.status_code == status
    if can_cancel is not None:
        assert response.context["can_cancel"] is can_cancel


@pytest.mark.parametrize(
    ("who", "restored"),
    [
        pytest.param("admin", True, id="admin"),
        pytest.param("member", False, id="member"),
    ],
)
def test_cancel_deletion_view(client, pending_team, who, restored):
    user = _admin(pending_team) if who == "admin" else _member(pending_team)
    client.force_login(user)

    response = client.post(reverse("teams:cancel_deletion", args=[pending_team.slug]))

    pending_team.refresh_from_db()
    assert pending_team.is_pending_deletion is not restored
    assert response.status_code == (302 if restored else 404)


def test_pending_team_is_not_a_default_team(pending_team):
    assert get_default_team_for_user(_member(pending_team)) is None


def test_api_key_for_pending_team_is_rejected(rf, pending_team):
    _, key = UserAPIKey.objects.create_key(name="k", user=_admin(pending_team), team=pending_team)
    request = Request(rf.get("/api/v2/chatbots/", HTTP_X_API_KEY=key))

    with pytest.raises(exceptions.AuthenticationFailed):
        ApiKeyAuthentication().authenticate(request)


def test_channels_of_pending_team_are_disabled(pending_team):
    channel = ExperimentChannelFactory.create(team=pending_team)
    assert channel.is_disabled


def test_pending_team_chatbots_are_frozen(pending_team):
    trigger = StaticTriggerFactory.create(experiment=ExperimentFactory.create(team=pending_team))
    active = StaticTriggerFactory.create(experiment=ExperimentFactory.create(team=TeamWithUsersFactory.create()))

    unfrozen = StaticTrigger.objects.get_all().exclude(frozen_experiment_q())

    assert trigger not in unfrozen
    assert active in unfrozen
