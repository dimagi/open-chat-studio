from io import BytesIO
from unittest import mock
from unittest.mock import Mock

import pytest
from mock.mock import patch

from apps.channels.datamodels import SlackMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import SlackChannel
from apps.chat.models import ChatMessage, ChatMessageType
from apps.files.models import File
from apps.service_providers.messaging_service import SlackService
from apps.service_providers.tracing import TraceInfo
from apps.slack.utils import make_session_external_id
from apps.utils.factories.channels import ExperimentChannelFactory


@pytest.fixture()
def slack_service():
    service = SlackService(slack_team_id="123", slack_installation_id=1)
    mock_client = Mock(
        conversations_list=Mock(
            return_value=[
                {"channels": [{"id": "123", "name": "channel1"}]},
                {"channels": [{"id": "345", "name": "channel2"}]},
            ]
        )
    )
    service.client = mock_client
    return service


def make_mock_file(name, content_type, size, file_data=b"filedata"):
    file = Mock(spec=File)
    file.name = name
    file.content_type = content_type
    file.content_size = size
    file.file = BytesIO(file_data)
    file.download_link.return_value = f"http://example.com/{name}"
    return file


@pytest.mark.django_db()
@patch("apps.chat.bots.TopicBot.process_input")
def test_handle_user_message(process_input, slack_channel, slack_service):
    process_input.return_value = ChatMessage(content="Hi", message_type=ChatMessageType.AI)
    session = SlackChannel.start_new_session(
        slack_channel.experiment,
        slack_channel,
        "SLACK_USER_ID",
        session_external_id=make_session_external_id("channel_id", "thread_ts"),
    )
    message = SlackMessage(
        participant_id="SLACK_USER_ID",
        channel_id="channel_id",
        thread_ts="thread_ts",
        message_text="Hello",
    )
    response = SlackChannel(slack_channel.experiment, slack_channel, session, slack_service).new_user_message(message)
    assert response.content == "Hi"


@pytest.mark.django_db()
@patch("apps.chat.bots.EventBot.get_user_message")
@patch("apps.chat.channels.SlackChannel.messaging_service")
def test_ad_hoc_bot_message(messaging_service, get_user_message, slack_channel):
    get_user_message.return_value = "Hi"
    session = SlackChannel.start_new_session(
        slack_channel.experiment,
        slack_channel,
        "SLACK_USER_ID",
        session_external_id=make_session_external_id("channel_id", "thread_ts"),
    )
    session.ad_hoc_bot_message("Hello", TraceInfo(name="slack test"))
    assert messaging_service.send_text_message.call_args_list == [
        mock.call("Hi", from_="", to="channel_id", thread_ts="thread_ts", platform=ChannelPlatform.SLACK)
    ]


@pytest.fixture()
def slack_channel(slack_provider) -> ExperimentChannel:
    return ExperimentChannelFactory(
        platform=ChannelPlatform.SLACK,
        messaging_provider=slack_provider,
        experiment__team=slack_provider.team,
        extra_data={
            "slack_team_id": "TEAM",
            "slack_channel_name": "CHANNEL_NAME",
        },
    )


@pytest.mark.django_db()
def test_send_message_to_user_with_file(slack_channel, slack_service):
    session = SlackChannel.start_new_session(
        slack_channel.experiment,
        slack_channel,
        "SLACK_USER_ID",
        session_external_id=make_session_external_id("channel_id", "thread_ts"),
    )
    channel = SlackChannel(
        experiment=slack_channel.experiment,
        experiment_channel=slack_channel,
        experiment_session=session,
        messaging_service=slack_service,
    )
    slack_service.client.files_upload_v2 = Mock()
    file = make_mock_file("document.pdf", "application/pdf", 2 * 1024 * 1024)

    with patch.object(channel, "send_text_to_user") as mock_send_text:
        channel.send_message_to_user("Here's your file", files=[file])

        slack_service.client.files_upload_v2.assert_called_once()
        mock_send_text.assert_called_once_with("Here's your file")
