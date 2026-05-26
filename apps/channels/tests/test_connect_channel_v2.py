"""
Tests for the v2 CommCare Connect channel implementation:
  - CommCareConnectSender (unit, DB-backed)
  - CommCareConnectChannel full pipeline (integration)

Generic platform-consent behavior is tested in test_consent_config_stage.py.
"""

import base64
import os
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.test import override_settings

from apps.channels.channels_v2.connect_channel import CommCareConnectSender
from apps.channels.channels_v2.pipeline import MessageProcessingContext
from apps.channels.clients.connect_client import CommCareConnectClient, Message, NewMessagePayload
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_commcare_connect_message
from apps.chat.exceptions import ChannelException
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ParticipantData
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ParticipantFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_participant_data(experiment, consent=True):
    """Create a participant + ParticipantData with optional commcare metadata."""
    connect_id = str(uuid4())
    channel_id = str(uuid4())
    encryption_key = os.urandom(32)
    participant = ParticipantFactory.create(
        identifier=connect_id, team=experiment.team, platform=ChannelPlatform.COMMCARE_CONNECT
    )
    participant_data = ParticipantData.objects.create(
        team=experiment.team,
        participant=participant,
        experiment=experiment,
        system_metadata={"commcare_connect_channel_id": channel_id, "consent": consent},
        encryption_key=base64.b64encode(encryption_key).decode("utf-8"),
    )
    return participant, participant_data, channel_id, encryption_key


def _encrypt_user_message(encryption_key, channel_id, text="Hello bot"):
    """Build an encrypted NewMessagePayload as CommCare Connect would send it."""
    client = CommCareConnectClient()
    ciphertext, tag, nonce = client._encrypt_message(key=encryption_key, message=text)
    message = Message(timestamp=1000, message_id=str(uuid4()), ciphertext=ciphertext, tag=tag, nonce=nonce)
    return NewMessagePayload(channel_id=channel_id, messages=[message])


# ---------------------------------------------------------------------------
# CommCareConnectSender
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
class TestCommCareConnectSender:
    def _make_sender(self, experiment, participant_data=None):
        """Build a bound sender. ``participant_data`` is what the context
        exposes via its cached_property -- tests pre-seed it directly to
        mirror what the cached_property does the first time it's read in
        the real pipeline (or pass None to simulate a missing row)."""
        sender = CommCareConnectSender()
        ctx = Mock(spec=MessageProcessingContext, experiment=experiment, participant_data=participant_data)
        sender.bind(ctx)
        return sender

    def test_send_text_calls_client_with_correct_args(self, experiment):
        participant, participant_data, channel_id, encryption_key = _make_participant_data(experiment, consent=True)
        sender = self._make_sender(experiment, participant_data=participant_data)

        with patch("apps.channels.channels_v2.connect_channel.CommCareConnectClient") as ClientMock:
            client_instance = ClientMock.return_value
            sender.send_text("Hello world", recipient=participant.identifier)

        client_instance.send_message_to_user.assert_called_once_with(
            channel_id=channel_id,
            message="Hello world",
            encryption_key=encryption_key,
        )

    def test_send_text_generates_missing_encryption_key(self, experiment):
        """When the encryption key is missing, the sender generates one and
        proceeds. The mobile app always calls ``get_key`` before decrypting,
        so it will read whatever key we used to encrypt the message."""
        participant, participant_data, channel_id, _ = _make_participant_data(experiment, consent=True)
        participant_data.encryption_key = ""
        participant_data.save()

        sender = self._make_sender(experiment, participant_data=participant_data)

        with patch("apps.channels.channels_v2.connect_channel.CommCareConnectClient") as ClientMock:
            sender.send_text("Hello", recipient=participant.identifier)

        participant_data.refresh_from_db()
        assert participant_data.encryption_key
        client_instance = ClientMock.return_value
        client_instance.send_message_to_user.assert_called_once_with(
            channel_id=channel_id,
            message="Hello",
            encryption_key=participant_data.get_encryption_key_bytes(),
        )

    def test_send_text_raises_when_no_participant_data(self, experiment):
        sender = self._make_sender(experiment, participant_data=None)

        with patch("apps.channels.channels_v2.connect_channel.CommCareConnectClient"):
            with pytest.raises(ChannelException, match="Participant data not found"):
                sender.send_text("Hi", recipient="ghost-participant")

    def test_send_text_raises_when_channel_id_missing(self, experiment):
        participant = ParticipantFactory.create(team=experiment.team, platform=ChannelPlatform.COMMCARE_CONNECT)
        participant_data = ParticipantData.objects.create(
            team=experiment.team,
            participant=participant,
            experiment=experiment,
            system_metadata={"consent": True},  # no channel_id
            encryption_key=base64.b64encode(os.urandom(32)).decode("utf-8"),
        )
        sender = self._make_sender(experiment, participant_data=participant_data)

        with patch("apps.channels.channels_v2.connect_channel.CommCareConnectClient"):
            with pytest.raises(ChannelException, match="channel_id is missing"):
                sender.send_text("Hi", recipient=participant.identifier)


# ---------------------------------------------------------------------------
# CommCareConnectChannel full pipeline integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
class TestCommCareConnectChannelIntegration:
    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="test-secret", COMMCARE_CONNECT_SERVER_ID="test-id")
    def test_bot_generates_and_sends_encrypted_message(self, experiment):
        """Full pipeline: task decrypts message, bot responds, sender encrypts and sends."""
        participant, participant_data, channel_id, encryption_key = _make_participant_data(experiment, consent=True)
        ExperimentChannelFactory.create(
            team=experiment.team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
        )
        payload = _encrypt_user_message(encryption_key, channel_id)
        experiment.create_new_version(make_default=True)

        with (
            patch("apps.chat.bots.PipelineBot.process_input") as mock_bot,
            patch("apps.channels.channels_v2.connect_channel.CommCareConnectClient") as ClientMock,
        ):
            mock_bot.return_value = ChatMessage(content="Hi human", message_type=ChatMessageType.AI)
            handle_commcare_connect_message(experiment.id, participant_data.id, payload["messages"])

        client_instance = ClientMock.return_value
        assert client_instance.send_message_to_user.call_count == 1
        call_kwargs = client_instance.send_message_to_user.call_args[1]
        assert call_kwargs["channel_id"] == channel_id
        assert call_kwargs["message"] == "Hi human"
        assert call_kwargs["encryption_key"] == encryption_key

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="test-secret", COMMCARE_CONNECT_SERVER_ID="test-id")
    def test_pipeline_aborts_silently_when_consent_revoked(self, experiment):
        """Pipeline aborts at consent check: bot is never invoked AND no
        message is sent back to the participant."""
        participant, participant_data, channel_id, encryption_key = _make_participant_data(experiment, consent=False)
        ExperimentChannelFactory.create(
            team=experiment.team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
        )
        payload = _encrypt_user_message(encryption_key, channel_id)
        experiment.create_new_version(make_default=True)

        with (
            patch("apps.chat.bots.PipelineBot.process_input") as mock_bot,
            patch("apps.channels.channels_v2.connect_channel.CommCareConnectClient") as ClientMock,
        ):
            handle_commcare_connect_message(experiment.id, participant_data.id, payload["messages"])

        mock_bot.assert_not_called()
        ClientMock.return_value.send_message_to_user.assert_not_called()
