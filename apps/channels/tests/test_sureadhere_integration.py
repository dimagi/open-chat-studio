from unittest.mock import patch

import pytest
from django.urls import reverse

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
    @patch("apps.chat.channels.SureAdhereChannel._get_bot_response")
    def test_sureadhere_channel_implementation(
        self,
        get_llm_response_mock,
        send_text_message,
        incoming_message,
        message_type,
        sureadhere_channel,
    ):
        get_llm_response_mock.return_value = ChatMessage(content="Hi")
        handle_sureadhere_message(
            sureadhere_tenant_id=sureadhere_channel.extra_data["sureadhere_tenant_id"], message_data=incoming_message
        )
        send_text_message.assert_called()

    @pytest.mark.django_db()
    @pytest.mark.parametrize("message", [sureadhere_messages.outbound_message()])
    @patch("apps.channels.tasks.handle_sureadhere_message")
    def test_outbound_message_ignored(self, handle_sureadhere_message_task, message, client):
        ExperimentChannelFactory(
            platform=ChannelPlatform.SUREADHERE,
            extra_data={
                "sureadhere_tenant_id": "6",
            },
        )
        url = reverse("channels:new_sureadhere_message", kwargs={"sureadhere_tenant_id": "6"})
        response = client.post(url, data=message, content_type="application/json")
        assert response.status_code == 200
        handle_sureadhere_message_task.assert_not_called()
