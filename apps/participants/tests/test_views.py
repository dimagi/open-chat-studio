import json
from unittest.mock import patch

import pytest
from django.http import QueryDict
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ParticipantData
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
def test_edit_participant_data(client, team_with_users):
    participant = ParticipantFactory(team=team_with_users)
    team = participant.team
    session = ExperimentSessionFactory(participant=participant, team=team, experiment__team=team)
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
    participant = ParticipantFactory(team=team_with_users, platform=ChannelPlatform.WHATSAPP)
    experiment = ExperimentFactory(team=team_with_users, working_version=None)
    ExperimentChannelFactory(team=team_with_users, experiment=experiment, platform=ChannelPlatform.WHATSAPP)
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
    assert response.status_code == 200
    assert response.headers.get("HX-Refresh") == "true"

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
    participant = ParticipantFactory(team=team_with_users, platform=ChannelPlatform.WHATSAPP)
    experiment = ExperimentFactory(team=team_with_users, working_version=None)
    ExperimentChannelFactory(team=team_with_users, experiment=experiment, platform=ChannelPlatform.WHATSAPP)
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
    assert "Invalid JSON" in response.content.decode()
    mock_task.delay.assert_not_called()


@pytest.mark.django_db()
def test_trigger_bot_filters_experiments_by_platform(client, team_with_users):
    """Test that only experiments with matching platform channels are shown"""
    participant = ParticipantFactory(team=team_with_users, platform=ChannelPlatform.WHATSAPP)
    # Experiment with WhatsApp channel (should be available)
    experiment_whatsapp = ExperimentFactory(team=team_with_users, working_version=None)
    ExperimentChannelFactory(team=team_with_users, experiment=experiment_whatsapp, platform=ChannelPlatform.WHATSAPP)
    # Experiment with Telegram channel (should not be available)
    experiment_telegram = ExperimentFactory(team=team_with_users, working_version=None)
    ExperimentChannelFactory(team=team_with_users, experiment=experiment_telegram, platform=ChannelPlatform.TELEGRAM)

    user = team_with_users.members.first()
    client.login(username=user.username, password="password")

    url = reverse(
        "participants:single-participant-home",
        kwargs={
            "team_slug": team_with_users.slug,
            "participant_id": participant.id,
        },
    )

    response = client.get(url)
    assert response.status_code == 200

    # Check that the form is in the context
    assert "actions" in response.context
    actions = response.context["actions"]
    assert len(actions) == 1

    # Get the form from the modal context
    form = actions[0].modal_context["form"]

    # Verify the form only includes experiments with matching platform
    available_experiments = list(form.fields["experiment"].queryset)
    assert experiment_whatsapp in available_experiments
    assert experiment_telegram not in available_experiments
