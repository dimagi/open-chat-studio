import json
from unittest.mock import patch

import pytest
from django.http import QueryDict
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.experiments.models import Participant, ParticipantData
from apps.participants.forms import TriggerBotForm
from apps.utils.factories.channels import ExperimentChannelFactory
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
@patch("apps.participants.views.trigger_bot_message_task")
def test_trigger_bot(mock_task, client, team_with_users):
    """Test that a bot can be triggered for a participant"""
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

    response = client.post(url, data)
    assert response.status_code == 302

    # Verify the task was called with correct data
    mock_task.delay.assert_called_once()
    call_args = mock_task.delay.call_args[0][0]
    assert call_args["identifier"] == participant.identifier
    assert call_args["platform"] == participant.platform
    assert call_args["experiment"] == str(experiment.public_id)
    assert call_args["prompt_text"] == "Hello, this is a test message"
    assert call_args["start_new_session"] is True
    assert call_args["session_data"] == {"key": "value"}


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
    mock_task.delay.assert_not_called()


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
