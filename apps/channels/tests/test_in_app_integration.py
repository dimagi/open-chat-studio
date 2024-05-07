from unittest.mock import patch

import pytest

from apps.channels.datamodels import SureAdhereMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_sureadhere_message
from apps.chat.channels import MESSAGE_TYPES
from apps.utils.factories.channels import ExperimentChannelFactory

from .message_examples import sureadhere_messages


@pytest.fixture()
def sureadhere_in_app_channel(sureadhere_provider):
    return ExperimentChannelFactory(
        platform=ChannelPlatform.IN_APP,
        messaging_provider=sureadhere_provider,
        experiment__team=sureadhere_provider.team,
        extra_data={"client_id": "6"},
    )


class TestSureAdhere:
    @pytest.mark.parametrize(
        ("message", "message_type"),
        [(sureadhere_messages.inbound_message(), "text")],
    )
    def test_parse_text_message(self, message, message_type):
        message = SureAdhereMessage.parse(message)
        assert message.chat_id == "6225"
        assert message.body == "Hi"
        assert message.content_type == MESSAGE_TYPES.TEXT

    @pytest.mark.django_db()
    @pytest.mark.parametrize(
        ("incoming_message", "message_type"),
        [(sureadhere_messages.inbound_message(), "text")],
    )
    @patch("apps.service_providers.messaging_service.SureAdhereService.send_text_message")
    @patch("apps.chat.channels.SureAdhereChannel._get_llm_response")
    def test_sureadhere_in_app_channel_implementation(
        self,
        _get_llm_response,
        send_text_message,
        incoming_message,
        message_type,
        sureadhere_in_app_channel,
    ):
        _get_llm_response.return_value = "Hi"
        handle_sureadhere_message(
            client_id=sureadhere_in_app_channel.extra_data.get("client_id", ""), message_data=incoming_message
        )
        send_text_message.assert_called()
