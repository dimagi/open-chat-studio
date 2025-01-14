import base64
import os
from unittest.mock import patch
from uuid import uuid4

import pytest

from apps.channels.clients.connect_client import CommCareConnectClient, Message, NewMessagePayload
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_connect_messaging_message
from apps.experiments.models import ParticipantData
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ParticipantFactory


def _setup(experiment, message_spec: dict | None = None) -> tuple:
    """
    message_spec example: {1736835441: "hi there"}
    """
    if not message_spec:
        message_spec = {1736835441: "Hi there bot"}

    team = experiment.team
    connect_id = uuid4().hex
    commcare_connect_channel_id = uuid4().hex

    encryption_key = os.urandom(32)
    participant = ParticipantFactory(identifier=connect_id, team=team, platform=ChannelPlatform.COMMCARE_CONNECT)
    part_data = ParticipantData.objects.create(
        team=team,
        participant=participant,
        system_metadata={"commcare_connect_channel_id": commcare_connect_channel_id},
        content_object=experiment,
        encryption_key=base64.b64encode(encryption_key).decode("utf-8"),
    )
    experiment_channel = ExperimentChannelFactory(
        team=team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
    )

    connect_client = CommCareConnectClient()
    messages = []
    for timestamp, message in message_spec.items():
        ciphertext_bytes, tag_bytes, nonce_bytes = connect_client._encrypt_message(key=encryption_key, message=message)
        messages.append(
            Message(
                timestamp=timestamp,
                message_id=uuid4().hex,
                ciphertext=base64.b64encode(ciphertext_bytes).decode(),
                tag=base64.b64encode(tag_bytes).decode(),
                nonce=base64.b64encode(nonce_bytes).decode(),
            )
        )

    payload = NewMessagePayload(channel_id=commcare_connect_channel_id, messages=messages)

    return commcare_connect_channel_id, encryption_key, experiment_channel, part_data, payload


@pytest.mark.django_db()
class TestHandleConnectMessageTask:
    @patch("apps.channels.tasks.CommCareConnectChannel")
    def test_participant_data_is_missing(self, CommCareConnectChannelMock, experiment, caplog):
        channel_instance = CommCareConnectChannelMock.return_value
        channel_id, _, _, participant_data, payload = _setup(experiment)
        participant_data.delete()

        handle_connect_messaging_message(payload)
        channel_instance.new_user_message.assert_not_called()
        assert caplog.messages[0] == f"No participant data found for channel_id: {channel_id}"

    @patch("apps.channels.tasks.CommCareConnectChannel")
    def test_experiment_channel_is_missing(self, CommCareConnectChannelMock, experiment, caplog):
        channel_instance = CommCareConnectChannelMock.return_value
        channel_id, _, experiment_channel, _, payload = _setup(experiment)
        experiment_channel.delete()

        handle_connect_messaging_message(payload)
        channel_instance.new_user_message.assert_not_called()
        assert caplog.messages[0] == f"No experiment channel found for participant channel_id: {channel_id}"

    @patch("apps.channels.tasks.CommCareConnectChannel")
    def test_multiple_messages_are_sorted_and_concatenated(self, CommCareConnectChannelMock, experiment):
        channel_instance = CommCareConnectChannelMock.return_value
        _, _, _, _, payload = _setup(experiment, message_spec={2: "I need to ask something", 1: "Hi bot"})

        handle_connect_messaging_message(payload)
        base_message = channel_instance.new_user_message.call_args[0][0]
        assert base_message.message_text == "Hi bot\n\nI need to ask something"

    @pytest.mark.django_db()
    @patch("apps.chat.bots.TopicBot.process_input")
    def test_bot_generate_and_sends_message(self, process_input, experiment):
        process_input.return_value = "Hi human"
        commcare_connect_channel_id, encryption_key, _, _, payload = _setup(experiment)

        with patch("apps.chat.channels.CommCareConnectClient") as ConnectClientMock:
            client_mock = ConnectClientMock.return_value
            handle_connect_messaging_message(payload)
            assert client_mock.send_message_to_user.call_count == 1
            call_kwargs = client_mock.send_message_to_user.call_args[1]
            assert call_kwargs["channel_id"] == commcare_connect_channel_id
            assert call_kwargs["message"] == "Hi human"
            assert call_kwargs["encryption_key"] == encryption_key
