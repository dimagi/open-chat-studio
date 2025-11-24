import base64
import hashlib
import hmac
import json
import os
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from apps.channels.clients.connect_client import CommCareConnectClient, Message, NewMessagePayload
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_commcare_connect_message
from apps.chat.channels import CommCareConnectChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.participants.models import ParticipantData

# TODO: Update Participant import
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory, ParticipantFactory


def _setup_participant(experiment) -> tuple:
    """Sets up a participant as if they went through the consent process"""
    team = experiment.team
    connect_id = str(uuid4())
    commcare_connect_channel_id = str(uuid4())

    encryption_key = os.urandom(32)
    participant = ParticipantFactory(identifier=connect_id, team=team, platform=ChannelPlatform.COMMCARE_CONNECT)
    part_data = ParticipantData.objects.create(
        team=team,
        participant=participant,
        system_metadata={"commcare_connect_channel_id": commcare_connect_channel_id, "consent": True},
        experiment=experiment,
        encryption_key=base64.b64encode(encryption_key).decode("utf-8"),
    )
    experiment_channel = ExperimentChannelFactory(
        team=team, experiment=experiment, platform=ChannelPlatform.COMMCARE_CONNECT
    )
    return commcare_connect_channel_id, encryption_key, experiment_channel, part_data


def _build_user_message(encryption_key, commcare_connect_channel_id, message_spec: dict | None = None):
    """Build the encrypted payload of a user message that CommCareConnect would send

    message_spec example: {1736835441: "hi there"}
    """
    if not message_spec:
        message_spec = {1736835441: "Hi there bot"}
    connect_client = CommCareConnectClient()
    messages = []
    for timestamp, message in message_spec.items():
        ciphertext, tag, nonce = connect_client._encrypt_message(key=encryption_key, message=message)
        messages.append(
            Message(
                timestamp=timestamp,
                message_id=str(uuid4()),
                ciphertext=ciphertext,
                tag=tag,
                nonce=nonce,
            )
        )

    return NewMessagePayload(channel_id=commcare_connect_channel_id, messages=messages)


@pytest.mark.django_db()
class TestHandleConnectMessageTask:
    @patch("apps.channels.tasks.CommCareConnectChannel")
    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_multiple_messages_are_sorted_and_concatenated(self, CommCareConnectChannelMock, experiment):
        channel_instance = CommCareConnectChannelMock.return_value
        commcare_connect_channel_id, encryption_key, experiment_channel, data = _setup_participant(experiment)
        payload = _build_user_message(
            encryption_key, commcare_connect_channel_id, message_spec={2: "I need to ask something", 1: "Hi bot"}
        )

        handle_commcare_connect_message(experiment_channel.id, data.id, payload["messages"])
        base_message = channel_instance.new_user_message.call_args[0][0]
        assert base_message.message_text == "Hi bot\n\nI need to ask something"

    @pytest.mark.django_db()
    @patch("apps.chat.bots.TopicBot.process_input")
    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_bot_generate_and_sends_message(self, process_input, experiment):
        process_input.return_value = ChatMessage(content="Hi human", message_type=ChatMessageType.AI)
        commcare_connect_channel_id, encryption_key, experiment_channel, data = _setup_participant(experiment)
        payload = _build_user_message(encryption_key, commcare_connect_channel_id)
        # The version will be used when chatting to the bot
        experiment.create_new_version(make_default=True)

        with patch("apps.chat.channels.CommCareConnectClient") as ConnectClientMock:
            client_mock = ConnectClientMock.return_value
            handle_commcare_connect_message(experiment_channel.id, data.id, payload["messages"])
            assert client_mock.send_message_to_user.call_count == 1
            call_kwargs = client_mock.send_message_to_user.call_args[1]
            assert call_kwargs["channel_id"] == commcare_connect_channel_id
            assert call_kwargs["message"] == "Hi human"
            assert call_kwargs["encryption_key"] == encryption_key


@pytest.mark.django_db()
class TestApiEndpoint:
    def _get_request_headers(self, payload: dict) -> dict:
        msg = json.dumps(payload).encode("utf-8")
        key = settings.COMMCARE_CONNECT_SERVER_SECRET.encode()
        digest = hmac.new(key=key, msg=msg, digestmod=hashlib.sha256).digest()
        return {
            "X-MAC-DIGEST": base64.b64encode(digest),
        }

    @patch("apps.channels.views.tasks.handle_commcare_connect_message", Mock())
    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_payload_passes_validation(self, client, experiment):
        commcare_connect_channel_id, encryption_key, _, _ = _setup_participant(experiment)
        payload = _build_user_message(encryption_key, commcare_connect_channel_id)

        response = client.post(
            reverse("channels:new_connect_message"),
            json.dumps(payload),
            headers=self._get_request_headers(payload),
            content_type="application/json",
        )
        assert response.status_code == 200

    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_invalid_payload(self, client, experiment):
        commcare_connect_channel_id, encryption_key, _, _ = _setup_participant(experiment)
        payload = _build_user_message(encryption_key, commcare_connect_channel_id)
        payload["messages"][0]["timestamp"] = None

        response = client.post(
            reverse("channels:new_connect_message"),
            json.dumps(payload),
            headers=self._get_request_headers(payload),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json() == {"messages": [{"timestamp": ["This field may not be null."]}]}

    @pytest.mark.parametrize(
        ("missing_record", "expected_response"),
        [
            ("participant_data", {"detail": "No participant data found"}),
            ("experiment_channel", {"detail": "No experiment channel found"}),
        ],
    )
    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_missing_records(self, missing_record, expected_response, client, experiment):
        commcare_connect_channel_id, encryption_key, experiment_channel, participant_data = _setup_participant(
            experiment
        )
        payload = _build_user_message(encryption_key, commcare_connect_channel_id)
        if missing_record == "participant_data":
            participant_data.delete()
        else:
            experiment_channel.delete()

        response = client.post(
            reverse("channels:new_connect_message"),
            json.dumps(payload),
            headers=self._get_request_headers(payload),
            content_type="application/json",
        )
        assert response.status_code == 404
        assert response.json() == expected_response


class TestCommCareConnectChannel:
    @pytest.mark.django_db()
    @override_settings(COMMCARE_CONNECT_SERVER_SECRET="123", COMMCARE_CONNECT_SERVER_ID="123")
    def test_get_encryption_key_generates_missing_key(self):
        """Missing encryption keys should be generated"""
        session = ExperimentSessionFactory(experiment_channel__platform=ChannelPlatform.COMMCARE_CONNECT)
        channel = CommCareConnectChannel.from_experiment_session(session)
        participant_data = ParticipantData.objects.create(
            team=session.team,
            participant=session.participant,
            experiment=session.experiment,
        )

        assert len(participant_data.encryption_key) == 0
        # A call to encryption_key() should generate a new key if one is missing
        key = channel.encryption_key
        participant_data.refresh_from_db()
        assert key is not None
        assert participant_data.get_encryption_key_bytes() == key
