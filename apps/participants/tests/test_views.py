import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.http import QueryDict
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession, Participant, ParticipantData
from apps.participants.forms import TriggerBotForm
from apps.teams.models import Flag
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.cost_tracking import UsageRecordFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
def test_edit_participant_data(client, team_with_users):
    participant = ParticipantFactory.create(team=team_with_users)
    team = participant.team
    session = ExperimentSessionFactory.create(participant=participant, team=team, experiment__team=team)
    user = participant.team.members.first()
    data = {"name": "A"}
    participant_data = ParticipantData.objects.create(
        team=team, experiment=session.experiment, participant=participant, data=data
    )
    client.login(username=user.username, password="password")

    url = reverse(
        "participants:edit-participant-data",
        kwargs={
            "team_slug": participant.team.slug,
            "participant_id": participant.id,
            "experiment_id": session.experiment.id,
        },
    )

    data["name"] = "B"
    query_data = QueryDict("", mutable=True)
    query_data.update({"participant-data": json.dumps(data)})
    client.post(url, query_data)
    participant_data.refresh_from_db()
    assert participant_data.data["name"] == "B"


@pytest.mark.django_db()
def test_single_participant_home_with_experiment_renders_session_table(client, team_with_users):
    """Regression: this page builds ChatbotSessionsTable without RequestConfig, so render_chatbot
    must not assume self.request is set."""
    participant = ParticipantFactory.create(team=team_with_users)
    session = ExperimentSessionFactory.create(
        participant=participant, team=team_with_users, experiment__team=team_with_users
    )
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")

    url = reverse(
        "participants:single-participant-home-with-experiment",
        kwargs={
            "team_slug": team_with_users.slug,
            "participant_id": participant.id,
            "experiment_id": session.experiment.id,
        },
    )
    response = client.get(url)
    assert response.status_code == 200
    # The table's Continue Chat button calls ocsContinueSessionChat, which only exists if this
    # page includes the launcher partial. Any page rendering ChatbotSessionsTable must include it.
    assert "ocsContinueSessionChat = function" in response.content.decode()


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@patch.object(ExperimentSession, "ad_hoc_bot_message", autospec=True)
def test_trigger_bot(mock_ad_hoc_bot_message, client, team_with_users, django_capture_on_commit_callbacks):
    """Test that a bot can be triggered for a participant.

    The task runs for real (eagerly) rather than being mocked: mocking it let this view keep calling
    the task with its old dict argument long after the signature changed (#4221).
    """
    participant = ParticipantFactory.create(team=team_with_users, platform=ChannelPlatform.WHATSAPP)
    experiment = ExperimentFactory.create(team=team_with_users, working_version=None)
    ExperimentChannelFactory.create(team=team_with_users, experiment=experiment, platform=ChannelPlatform.WHATSAPP)
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")

    url = reverse(
        "participants:trigger_bot",
        kwargs={
            "team_slug": team_with_users.slug,
            "participant_id": participant.id,
        },
    )

    data = {
        "prompt_text": "Hello, this is a test message",
        "experiment": experiment.id,
        "start_new_session": True,
        "session_data": '{"key": "value"}',
    }

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(url, data)
    assert response.status_code == 302

    # The view creates the session up front so the task can look it up by external ID
    session = ExperimentSession.objects.get(participant__identifier=participant.identifier, experiment=experiment)
    assert session.experiment_channel.platform == ChannelPlatform.WHATSAPP
    assert session.state == {"key": "value"}

    mock_ad_hoc_bot_message.assert_called_once()
    called_session, prompt_text = mock_ad_hoc_bot_message.call_args.args[:2]
    assert called_session == session
    assert prompt_text == "Hello, this is a test message"
    assert mock_ad_hoc_bot_message.call_args.kwargs["message_text"] is None


@pytest.mark.django_db()
@patch("apps.participants.views.trigger_bot_message_task")
def test_trigger_bot_on_disabled_channel(mock_task, client, team_with_users):
    """The channel kill-switch blocks outbound triggers too: no session is opened, nothing is sent."""
    participant = ParticipantFactory.create(team=team_with_users, platform=ChannelPlatform.WHATSAPP)
    experiment = ExperimentFactory.create(team=team_with_users, working_version=None)
    ExperimentChannelFactory.create(
        team=team_with_users, experiment=experiment, platform=ChannelPlatform.WHATSAPP, enabled=False
    )
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")

    url = reverse(
        "participants:trigger_bot",
        kwargs={
            "team_slug": team_with_users.slug,
            "participant_id": participant.id,
        },
    )

    response = client.post(
        url,
        {
            "prompt_text": "Hello, this is a test message",
            "experiment": experiment.id,
            "start_new_session": True,
            "session_data": "{}",
        },
    )

    assert response.status_code == 200
    assert "channel for this chatbot is disabled" in response.content.decode()
    mock_task.delay_on_commit.assert_not_called()
    assert not ExperimentSession.objects.filter(experiment=experiment).exists()


@pytest.mark.django_db()
@patch("apps.participants.views.trigger_bot_message_task")
def test_trigger_bot_with_invalid_json(mock_task, client, team_with_users):
    """Test that trigger bot fails with invalid session_data JSON"""
    participant = ParticipantFactory.create(team=team_with_users, platform=ChannelPlatform.WHATSAPP)
    experiment = ExperimentFactory.create(team=team_with_users, working_version=None)
    ExperimentChannelFactory.create(team=team_with_users, experiment=experiment, platform=ChannelPlatform.WHATSAPP)
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")

    url = reverse(
        "participants:trigger_bot",
        kwargs={
            "team_slug": team_with_users.slug,
            "participant_id": participant.id,
        },
    )

    data = {
        "prompt_text": "Hello, this is a test message",
        "experiment": experiment.id,
        "start_new_session": False,
        "session_data": "not valid json",
    }

    response = client.post(url, data)
    assert response.status_code == 200
    mock_task.delay_on_commit.assert_not_called()


@pytest.mark.django_db()
def test_trigger_bot_form_filters_experiments_by_platform(team_with_users):
    """Test that only experiments with matching platform channels are shown"""
    participant = ParticipantFactory.create(team=team_with_users, platform=ChannelPlatform.WHATSAPP)
    # Experiment with WhatsApp channel (should be available)
    experiment_whatsapp = ExperimentFactory.create(team=team_with_users, working_version=None)
    ExperimentChannelFactory.create(
        team=team_with_users, experiment=experiment_whatsapp, platform=ChannelPlatform.WHATSAPP
    )
    # Experiment with Telegram channel (should not be available)
    experiment_telegram = ExperimentFactory.create(team=team_with_users, working_version=None)
    ExperimentChannelFactory.create(
        team=team_with_users, experiment=experiment_telegram, platform=ChannelPlatform.TELEGRAM
    )

    available_experiments = list(TriggerBotForm(participant=participant).fields["experiment"].queryset)
    assert experiment_whatsapp in available_experiments
    assert experiment_telegram not in available_experiments


@pytest.mark.django_db()
def test_create_participant_get(client, team_with_users):
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")
    url = reverse("participants:participant_new", kwargs={"team_slug": team_with_users.slug})

    response = client.get(url)

    assert response.status_code == 200
    assert b"Create Participant" in response.content


@pytest.mark.django_db()
def test_create_participant_post_success_redirects_to_detail(client, team_with_users):
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")
    url = reverse("participants:participant_new", kwargs={"team_slug": team_with_users.slug})

    response = client.post(
        url,
        {"identifier": "alice@example.com", "platform": ChannelPlatform.WEB, "name": "Alice"},
    )

    participant = Participant.objects.get(team=team_with_users, identifier="alice@example.com")
    assert participant.platform == ChannelPlatform.WEB
    assert participant.name == "Alice"
    assert response.status_code == 302
    assert response["Location"] == reverse(
        "participants:single-participant-home",
        kwargs={"team_slug": team_with_users.slug, "participant_id": participant.id},
    )


@pytest.mark.django_db()
def test_create_participant_duplicate_shows_error_with_link(client, team_with_users):
    existing = ParticipantFactory.create(
        team=team_with_users, platform=ChannelPlatform.WEB, identifier="alice@example.com"
    )
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")
    url = reverse("participants:participant_new", kwargs={"team_slug": team_with_users.slug})

    response = client.post(
        url,
        {"identifier": "alice@example.com", "platform": ChannelPlatform.WEB, "name": "Alice"},
    )

    assert response.status_code == 200
    assert existing.get_absolute_url().encode() in response.content
    assert Participant.objects.filter(team=team_with_users, identifier="alice@example.com").count() == 1


@pytest.mark.django_db()
def test_create_participant_missing_fields_shows_field_errors(client, team_with_users):
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")
    url = reverse("participants:participant_new", kwargs={"team_slug": team_with_users.slug})

    response = client.post(url, {"identifier": "", "platform": "", "name": ""})

    assert response.status_code == 200
    assert not Participant.objects.filter(team=team_with_users).exists()


@pytest.mark.django_db()
def test_participant_home_shows_create_action(client, team_with_users):
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")
    url = reverse("participants:participant_home", kwargs={"team_slug": team_with_users.slug})

    response = client.get(url)

    assert response.status_code == 200
    create_url = reverse("participants:participant_new", kwargs={"team_slug": team_with_users.slug})
    assert create_url.encode() in response.content


def _enable_cost_flag(team):
    flag, _ = Flag.objects.get_or_create(name="flag_ai_cost_monitoring")
    flag.teams.add(team)
    flag.flush()


@pytest.mark.django_db()
class TestParticipantTableCostColumn:
    """The participants table shows a 30-day cost column only when the team has
    `flag_ai_cost_monitoring`."""

    def _get_table(self, client, team):
        user = team.members.first()
        client.login(username=user.username, password="password")
        url = reverse("participants:participant_table", kwargs={"team_slug": team.slug})
        return client.get(url)

    def test_column_absent_when_flag_off(self, client, team_with_users):
        ParticipantFactory.create(team=team_with_users)

        response = self._get_table(client, team_with_users)

        assert response.status_code == 200
        assert "Cost (30d)" not in response.content.decode()

    def test_column_shows_last_30_day_cost(self, client, team_with_users):
        _enable_cost_flag(team_with_users)
        participant = ParticipantFactory.create(team=team_with_users)
        UsageRecordFactory.create(
            team=team_with_users,
            participant=participant,
            cost=Decimal("1.23"),
            at=timezone.now() - timedelta(days=1),
        )
        UsageRecordFactory.create(
            team=team_with_users,
            participant=participant,
            cost=Decimal("9.99"),
            at=timezone.now() - timedelta(days=40),
        )

        response = self._get_table(client, team_with_users)

        content = response.content.decode()
        assert "Cost (30d)" in content
        assert "$1.23" in content
        assert "$11.22" not in content

    def test_participant_without_usage_shows_zero(self, client, team_with_users):
        _enable_cost_flag(team_with_users)
        ParticipantFactory.create(team=team_with_users)

        response = self._get_table(client, team_with_users)

        assert "$0.00" in response.content.decode()
