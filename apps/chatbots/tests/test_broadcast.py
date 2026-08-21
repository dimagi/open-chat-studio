import re
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chatbots.forms import BroadcastMessageForm
from apps.chatbots.tasks import send_broadcast_message
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def experiment():
    return ExperimentFactory(team=TeamWithUsersFactory())


@pytest.fixture()
def logged_in_client(client, experiment):
    client.force_login(experiment.team.members.first())
    return client


def _broadcast_url(experiment):
    return reverse("chatbots:broadcast_message", args=[experiment.team.slug, experiment.id])


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
    assert delay.call_count == 3, "each (participant, channel) pair must be messaged exactly once"
    assert messaged == {latest_telegram.id, on_whatsapp_too.id, other_participant.id}
    assert {call.kwargs["message"] for call in delay.call_args_list} == {"Hi all"}


@pytest.mark.django_db()
def test_chatbot_home_offers_only_the_broadcastable_channels(experiment, logged_in_client):
    """The API, web and evaluation channels can't be broadcast on, so they aren't offered."""
    telegram = ExperimentChannelFactory(team=experiment.team, experiment=experiment, platform=ChannelPlatform.TELEGRAM)
    ExperimentChannel.objects.get_team_api_channel(experiment.team)
    ExperimentChannel.objects.get_team_web_channel(experiment.team)
    ExperimentChannel.objects.get_team_evaluations_channel(experiment.team)

    response = logged_in_client.get(reverse("chatbots:single_chatbot_home", args=[experiment.team.slug, experiment.id]))

    assert response.status_code == 200
    assert list(response.context["broadcast_channels"]) == [telegram]
    content = response.content.decode()
    assert "Broadcast message" in content
    assert _broadcast_url(experiment) in content


@pytest.mark.django_db()
def test_broadcast_modal_arms_the_whatsapp_template_warning(experiment, logged_in_client):
    """The warning is driven by which of the rendered channels are WhatsApp ones."""
    ExperimentChannelFactory(team=experiment.team, experiment=experiment, platform=ChannelPlatform.TELEGRAM)
    whatsapp = ExperimentChannelFactory(team=experiment.team, experiment=experiment, platform=ChannelPlatform.WHATSAPP)

    content = logged_in_client.get(
        reverse("chatbots:single_chatbot_home", args=[experiment.team.slug, experiment.id])
    ).content.decode()

    assert re.search(rf"whatsappChannels:\s*\[\s*'{whatsapp.id}',\s*\]", content)
    assert "new_bot_message" in content
    assert "whatsapp_meta_cloud_api/#create-the-required-template-in-meta-business-manager" in content


@pytest.mark.django_db()
def test_chatbot_home_hides_the_broadcast_button_without_a_channel(experiment, logged_in_client):
    response = logged_in_client.get(reverse("chatbots:single_chatbot_home", args=[experiment.team.slug, experiment.id]))

    assert response.status_code == 200
    assert "Broadcast message" not in response.content.decode()


@pytest.mark.django_db()
@patch("apps.chatbots.views.send_broadcast_message.delay")
def test_broadcast_view_queues_the_selected_channels(delay, experiment, logged_in_client):
    telegram = ExperimentChannelFactory(team=experiment.team, experiment=experiment, platform=ChannelPlatform.TELEGRAM)

    response = logged_in_client.post(
        _broadcast_url(experiment), data={"channels": [telegram.id], "message": "We're back online"}
    )

    assert response.status_code == 302
    delay.assert_called_once_with(experiment_id=experiment.id, channel_ids=[telegram.id], message="We're back online")


@pytest.mark.django_db()
@patch("apps.chatbots.views.send_broadcast_message.delay")
def test_broadcast_view_rejects_a_chatbot_that_is_not_editable(delay, experiment, logged_in_client):
    """An archived chatbot can't be broadcast on, even by POSTing the endpoint directly."""
    telegram = ExperimentChannelFactory(team=experiment.team, experiment=experiment, platform=ChannelPlatform.TELEGRAM)
    experiment.archive()

    response = logged_in_client.post(_broadcast_url(experiment), data={"channels": [telegram.id], "message": "Hi"})

    assert response.status_code == 404
    delay.assert_not_called()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("channel", "message"),
    [
        pytest.param(None, "Hi", id="no-channel-selected"),
        pytest.param("other-chatbot", "Hi", id="channel-of-another-chatbot"),
        pytest.param("disabled", "Hi", id="disabled-channel"),
        pytest.param("own", "", id="empty-message"),
        pytest.param("own", "x" * (BroadcastMessageForm.MESSAGE_CHAR_LIMIT + 1), id="message-over-char-limit"),
    ],
)
@patch("apps.chatbots.views.send_broadcast_message.delay")
def test_broadcast_view_rejects_invalid_input(delay, channel, message, experiment, logged_in_client):
    """Nothing is queued unless a live channel of *this* chatbot and a message within the limit are given."""
    channels = {
        "own": ExperimentChannelFactory(team=experiment.team, experiment=experiment),
        "other-chatbot": ExperimentChannelFactory(team=experiment.team),
        "disabled": ExperimentChannelFactory(
            team=experiment.team, experiment=experiment, platform=ChannelPlatform.WHATSAPP, enabled=False
        ),
    }
    data = {"message": message}
    if channel:
        data["channels"] = [channels[channel].id]

    response = logged_in_client.post(_broadcast_url(experiment), data=data)

    assert response.status_code == 302
    delay.assert_not_called()
