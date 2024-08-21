from unittest import mock

import pytest
from mock.mock import patch

from apps.channels.datamodels import SlackMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import SlackChannel
from apps.slack.utils import make_session_external_id
from apps.utils.factories.channels import ExperimentChannelFactory


@pytest.mark.django_db()
@patch("apps.chat.bots.TopicBot.process_input")
def test_handle_user_message(process_input, slack_channel):
    process_input.return_value = "Hi"
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
    response = SlackChannel(
        slack_channel.experiment, slack_channel, session, send_response_to_user=False
    ).new_user_message(message)
    assert response == "Hi"


@pytest.mark.django_db()
@patch("apps.chat.bots.TopicBot.process_input")
@patch("apps.chat.channels.SlackChannel.messaging_service")
def test_ad_hoc_bot_message(messaging_service, process_input, slack_channel):
    process_input.return_value = "Hi"
    session = SlackChannel.start_new_session(
        slack_channel.experiment,
        slack_channel,
        "SLACK_USER_ID",
        session_external_id=make_session_external_id("channel_id", "thread_ts"),
    )
    session.ad_hoc_bot_message("Hello")
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
