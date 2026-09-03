import pytest
from django.template.loader import get_template
from django.urls import reverse

from apps.events.event_log import EventLogStatusChoices
from apps.teams.backends import SUPER_ADMIN_GROUP, add_user_to_team, create_default_groups
from apps.utils.factories.events import ScheduledTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def authed_client(team_with_users, client):
    user = team_with_users.members.first()
    client.force_login(user)
    return client


def _super_admin_client(client, team):
    create_default_groups()
    user = UserFactory.create()
    add_user_to_team(team, user, groups=[SUPER_ADMIN_GROUP])
    client.force_login(user)
    return client


@pytest.mark.django_db()
def test_create_scheduled_event_view_creates_trigger(experiment, client, team_with_users):
    _super_admin_client(client, team_with_users)
    future_date = "2099-01-01"
    url = reverse(
        "chatbots:events:scheduled_event_new",
        args=[experiment.team.slug, experiment.id],
    )
    response = client.post(
        url,
        {"action_type": "log", "trigger_date": future_date, "trigger_time": "09:00", "timezone": "UTC"},
    )
    assert response.status_code == 302
    trigger = experiment.scheduled_triggers.get()
    assert str(trigger.trigger_date) == future_date
    assert str(trigger.trigger_time) == "09:00:00"
    assert trigger.timezone == "UTC"


@pytest.mark.django_db()
def test_create_scheduled_event_view_rejects_past_date(experiment, client, team_with_users):
    _super_admin_client(client, team_with_users)
    url = reverse(
        "chatbots:events:scheduled_event_new",
        args=[experiment.team.slug, experiment.id],
    )
    response = client.post(
        url,
        {"action_type": "log", "trigger_date": "2020-01-01", "trigger_time": "09:00", "timezone": "UTC"},
    )
    assert response.status_code == 200
    assert not experiment.scheduled_triggers.exists()
    assert b"The scheduled time must be in the future" in response.content


@pytest.mark.parametrize(
    "action_type",
    ["log", "send_message_to_bot", "end_conversation", "schedule_trigger", "pipeline_start"],
)
@pytest.mark.django_db()
def test_action_params_form_view_renders_each_action_type(action_type, experiment, authed_client):
    url = reverse(
        "chatbots:events:action_params_form",
        args=[experiment.team.slug, experiment.id],
    )
    response = authed_client.get(url, {"action_type": action_type})
    assert response.status_code == 200


@pytest.mark.django_db()
def test_action_params_form_view_400_for_invalid_action_type(experiment, authed_client):
    url = reverse(
        "chatbots:events:action_params_form",
        args=[experiment.team.slug, experiment.id],
    )
    response = authed_client.get(url, {"action_type": "bogus"})
    assert response.status_code == 400


@pytest.mark.django_db()
def test_scheduled_views_are_scoped_to_request_team(client, team_with_users):
    other_team = TeamWithUsersFactory.create()
    other_experiment = ExperimentFactory.create(team=other_team)
    other_trigger = ScheduledTriggerFactory.create(experiment=other_experiment)
    _super_admin_client(client, team_with_users)

    new_url = reverse("chatbots:events:scheduled_event_new", args=[team_with_users.slug, other_experiment.id])
    assert client.get(new_url).status_code == 404

    edit_url = reverse(
        "chatbots:events:scheduled_event_edit",
        args=[team_with_users.slug, other_experiment.id, other_trigger.id],
    )
    assert client.get(edit_url).status_code == 404

    logs_url = reverse(
        "chatbots:events:scheduled_logs_view",
        args=[team_with_users.slug, other_experiment.id, other_trigger.id],
    )
    assert client.get(logs_url).status_code == 404

    delete_url = reverse(
        "chatbots:events:scheduled_event_delete",
        args=[team_with_users.slug, other_experiment.id, other_trigger.id],
    )
    assert client.post(delete_url).status_code == 404

    toggle_url = reverse(
        "chatbots:events:scheduled_event_toggle",
        args=[team_with_users.slug, other_experiment.id, other_trigger.id],
    )
    assert client.post(toggle_url).status_code == 404


@pytest.mark.django_db()
def test_scheduled_delete_and_toggle_reject_get_requests(client, team_with_users):
    team_experiment = ExperimentFactory.create(team=team_with_users)
    trigger = ScheduledTriggerFactory.create(experiment=team_experiment)
    _super_admin_client(client, team_with_users)

    delete_url = reverse(
        "chatbots:events:scheduled_event_delete",
        args=[team_with_users.slug, team_experiment.id, trigger.id],
    )
    assert client.get(delete_url).status_code == 405

    toggle_url = reverse(
        "chatbots:events:scheduled_event_toggle",
        args=[team_with_users.slug, team_experiment.id, trigger.id],
    )
    assert client.get(toggle_url).status_code == 405


@pytest.mark.django_db()
def test_event_logs_template_renders_without_session(client, team_with_users):
    trigger = ScheduledTriggerFactory.create(experiment=ExperimentFactory.create(team=team_with_users))
    trigger.event_logs.create(status=EventLogStatusChoices.SUCCESS, log="ok")

    template = get_template("events/components/event_logs.html")
    html = template.render(
        {
            "trigger": trigger,
            "event_logs": trigger.event_logs.all(),
            "team": team_with_users,
            "include_session": True,
        }
    )
    assert "No session" in html
    assert "Session Details" not in html
