from unittest.mock import patch

import pytest

from apps.channels.datamodels import SureAdhereMessage
from apps.channels.models import ChannelPlatform
from apps.channels.tasks import handle_sureadhere_message
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.models import ChatMessage
from apps.utils.factories.channels import ExperimentChannelFactory

from .message_examples import sureadhere_messages


@pytest.fixture()
def sureadhere_channel(sureadhere_provider):
    return ExperimentChannelFactory(
        platform=ChannelPlatform.SUREADHERE,
        messaging_provider=sureadhere_provider,
        experiment__team=sureadhere_provider.team,
        extra_data={"sureadhere_tenant_id": "12"},
    )


class TestSureAdhere:
    @pytest.mark.parametrize(
        ("message", "message_type"),
        [(sureadhere_messages.inbound_message(), "text")],
    )
    def test_parse_text_message(self, message, message_type):
        message = SureAdhereMessage.parse(message)
        assert message.participant_id == "6225"
        assert message.message_text == "Hi"
        assert message.content_type == MESSAGE_TYPES.TEXT

    @pytest.mark.django_db()
    @pytest.mark.parametrize(
        ("incoming_message", "message_type"),
        [(sureadhere_messages.inbound_message(), "text")],
    )
    @patch("apps.service_providers.messaging_service.SureAdhereService.send_text_message")
    @patch("apps.chat.bots.PipelineBot.process_input")
    def test_sureadhere_channel_implementation(
        self,
        bot_process_input,
        send_text_message,
        incoming_message,
        message_type,
        sureadhere_channel,
    ):
        bot_process_input.return_value = ChatMessage(content="Hi")
        handle_sureadhere_message(
            sureadhere_tenant_id=sureadhere_channel.extra_data["sureadhere_tenant_id"], message_data=incoming_message
        )
        send_text_message.assert_called()
