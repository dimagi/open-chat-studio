from unittest.mock import patch

import pytest

from apps.channels.models import ChannelPlatform
from apps.chatbots.tasks import send_broadcast_message
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.fixture()
def experiment():
    return ExperimentFactory()


@pytest.mark.django_db()
@patch("apps.chatbots.tasks.send_broadcast_message_to_session.delay")
def test_broadcast_reaches_each_participant_once_per_selected_channel(delay, experiment):
    """One send per (participant, channel), against the participant's live session."""
    team = experiment.team
    telegram = ExperimentChannelFactory(team=team, experiment=experiment, platform=ChannelPlatform.TELEGRAM)
    whatsapp = ExperimentChannelFactory(team=team, experiment=experiment, platform=ChannelPlatform.WHATSAPP)
    unselected = ExperimentChannelFactory(team=team, experiment=experiment, platform=ChannelPlatform.SLACK)

    participant = ParticipantFactory(team=team)
    # An older session on the same channel: superseded, so it must not be messaged.
    ExperimentSessionFactory(experiment=experiment, participant=participant, experiment_channel=telegram)
    latest_telegram = ExperimentSessionFactory(
        experiment=experiment, participant=participant, experiment_channel=telegram
    )
    on_whatsapp_too = ExperimentSessionFactory(
        experiment=experiment, participant=participant, experiment_channel=whatsapp
    )
    other_participant = ExperimentSessionFactory(experiment=experiment, experiment_channel=whatsapp)
    ExperimentSessionFactory(experiment=experiment, experiment_channel=unselected)
    # A participant of a different chatbot is not part of this broadcast.
    ExperimentSessionFactory(experiment=ExperimentFactory(team=team), experiment_channel=telegram)

    send_broadcast_message(experiment_id=experiment.id, channel_ids=[telegram.id, whatsapp.id], message="Hi all")

    messaged = {call.kwargs["session_id"] for call in delay.call_args_list}
    assert messaged == {latest_telegram.id, on_whatsapp_too.id, other_participant.id}
    assert {call.kwargs["message"] for call in delay.call_args_list} == {"Hi all"}
