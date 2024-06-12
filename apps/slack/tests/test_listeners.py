from unittest.mock import Mock

import pytest
from django.utils import timezone
from slack_bolt import BoltContext

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession
from apps.slack.models import SlackInstallation
from apps.slack.slack_listeners import new_message
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.langchain import mock_experiment_llm

SLACK_TEAM_ID = "SLACK_TEAM_ID"
SLACK_CHANNEL_ID = "SLACK_CHANNEL_ID"
BOT_USER_ID = "BOT_USER"
BOT_MENTION_EVENT = {
    "type": "message",
    "channel": SLACK_CHANNEL_ID,
    "event_ts": "1358878755.000001",
    "ts": "1358878759.000001",
    "team": SLACK_TEAM_ID,
    "text": f"<@{BOT_USER_ID}> new message",
    "user": "SLACK_USER_ID",
}
CHANNEL_MESSAGE_EVENT = {
    "type": "message",
    "channel": SLACK_CHANNEL_ID,
    "event_ts": "1358878755.000001",
    "ts": "1358878759.000001",
    "team": SLACK_TEAM_ID,
    "text": "new message",
    "user": "SLACK_USER_ID",
}
THREAD_REPLY_EVENT = {
    "type": "message",
    "channel": SLACK_CHANNEL_ID,
    "event_ts": "1358878755.000001",
    "ts": "1358878759.000001",
    "thread_ts": BOT_MENTION_EVENT["ts"],
    "team": SLACK_TEAM_ID,
    "text": "thread reply",
    "user": "SLACK_USER_ID",
}


@pytest.mark.django_db()
@pytest.mark.usefixtures("experiment_channel")
def test_response_to_bot_mention_in_assigned_channel(bolt_context):
    with mock_experiment_llm(None, responses=["Hello"]):
        new_message(BOT_MENTION_EVENT, bolt_context)
    assert bolt_context.say.call_args_list == [(("Hello",), {"thread_ts": BOT_MENTION_EVENT["ts"]})]
    assert ExperimentSession.objects.count() == 1


@pytest.mark.django_db()
def test_response_to_bot_mention_in_unassigned_channel(bolt_context):
    new_message(BOT_MENTION_EVENT, bolt_context)
    assert bolt_context.say.call_args_list == [
        (("There are no bots associated with this channel.",), {"thread_ts": BOT_MENTION_EVENT["ts"]})
    ]


@pytest.mark.django_db()
@pytest.mark.usefixtures("experiment_channel")
def test_ignores_messages_in_assigned_channel(bolt_context):
    new_message(CHANNEL_MESSAGE_EVENT, bolt_context)
    assert not bolt_context.say.called


@pytest.mark.django_db()
def test_ignores_messages_from_unassigned_channel(bolt_context):
    new_message(CHANNEL_MESSAGE_EVENT, bolt_context)
    assert not bolt_context.say.called


@pytest.mark.usefixtures("experiment_channel")
def test_responds_to_session_thread(bolt_context):
    bolt_context["say"] = Mock()
    with mock_experiment_llm(None, responses=["Hello"]):
        new_message(BOT_MENTION_EVENT, bolt_context)
        assert bolt_context.say.call_args_list == [(("Hello",), {"thread_ts": BOT_MENTION_EVENT["ts"]})]
        assert ExperimentSession.objects.count() == 1

    with mock_experiment_llm(None, responses=["How can I help?"]):
        bolt_context["say"] = Mock()
        new_message(THREAD_REPLY_EVENT, bolt_context)

    assert bolt_context.say.call_args_list == [(("How can I help?",), {"thread_ts": THREAD_REPLY_EVENT["thread_ts"]})]
    assert ExperimentSession.objects.count() == 1


@pytest.mark.usefixtures("experiment_channel")
def test_ignores_non_session_thread(bolt_context):
    bolt_context["say"] = Mock()

    new_message(CHANNEL_MESSAGE_EVENT, bolt_context)
    assert not bolt_context.say.called
    assert ExperimentSession.objects.count() == 0

    bolt_context["say"] = Mock()
    new_message(THREAD_REPLY_EVENT, bolt_context)

    assert not bolt_context.say.called
    assert ExperimentSession.objects.count() == 0


@pytest.fixture()
def slack_install(team_with_users):
    return SlackInstallation.objects.create(
        team=team_with_users,
        client_id="123",
        app_id="test app",
        user_id="123",
        installed_at=timezone.now(),
        slack_team_id="SLACK_TEAM_ID",
        bot_user_id="BOT_USER",
    )


@pytest.fixture()
def bolt_context(slack_install):
    return BoltContext(
        {
            "team_id": "SLACK_TEAM_ID",
            "slack_install": slack_install,
            "team": slack_install.team,
            "bot_user_id": BOT_USER_ID,
            "say": Mock(),
        }
    )


@pytest.fixture()
def experiment_channel(experiment):
    return ExperimentChannelFactory(
        experiment=experiment, platform=ChannelPlatform.SLACK, extra_data={"slack_channel_id": SLACK_CHANNEL_ID}
    )
