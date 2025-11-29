from unittest.mock import MagicMock, Mock

import pytest
from django.utils import timezone
from slack_bolt import BoltContext

from apps.channels.const import SLACK_ALL_CHANNELS
from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession
from apps.service_providers.models import MessagingProviderType
from apps.slack.models import SlackInstallation
from apps.slack.slack_listeners import new_message
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.service_provider_factories import MessagingProviderFactory

SLACK_TEAM_ID = "SLACK_TEAM_ID"
SLACK_CHANNEL_ID = "SLACK_CHANNEL_ID"
BOT_USER_ID = "BOT123"
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
    """Message is processed when there is an experiment channel assigned to this slack channel"""
    bolt_context.client.chat_postMessage = MagicMock()
    new_message(BOT_MENTION_EVENT, bolt_context)

    bolt_context.client.chat_postMessage.assert_called_once_with(
        channel=BOT_MENTION_EVENT["channel"],
        text="new message",  # test bot echo's input
        thread_ts=BOT_MENTION_EVENT["ts"],
    )
    assert ExperimentSession.objects.count() == 1


@pytest.mark.django_db()
def test_response_to_bot_mention_in_unassigned_channel(bolt_context):
    """Error message is returned when there is no experiment channel"""
    new_message(BOT_MENTION_EVENT, bolt_context)
    assert bolt_context.say.call_args_list == [
        (("Unable to find a bot to respond to your message.",), {"thread_ts": BOT_MENTION_EVENT["ts"]})
    ]


@pytest.mark.django_db()
@pytest.mark.usefixtures("experiment_channel")
def test_ignores_messages_in_assigned_channel(bolt_context):
    """Normal channel messages are ignored (no bot mention)"""
    new_message(CHANNEL_MESSAGE_EVENT, bolt_context)
    assert not bolt_context.say.called


@pytest.mark.django_db()
def test_ignores_messages_from_unassigned_channel(bolt_context):
    """Normal channel messages are ignored (no bot mention)"""
    new_message(CHANNEL_MESSAGE_EVENT, bolt_context)
    assert not bolt_context.say.called


@pytest.mark.usefixtures("experiment_channel")
def test_responds_to_session_thread(bolt_context):
    bolt_context.client.chat_postMessage = Mock()
    new_message(BOT_MENTION_EVENT, bolt_context)
    bolt_context.client.chat_postMessage.assert_called_with(
        channel=BOT_MENTION_EVENT["channel"],
        text="new message",
        thread_ts=BOT_MENTION_EVENT["ts"],
    )

    assert ExperimentSession.objects.count() == 1

    bolt_context.client.chat_postMessage.reset_mock()
    new_message(THREAD_REPLY_EVENT, bolt_context)

    bolt_context.client.chat_postMessage.assert_called_with(
        channel=THREAD_REPLY_EVENT["channel"],
        text="thread reply",
        thread_ts=THREAD_REPLY_EVENT["thread_ts"],
    )
    assert ExperimentSession.objects.count() == 1


@pytest.mark.usefixtures("experiment_channel")
def test_ignores_non_session_thread(bolt_context):
    new_message(CHANNEL_MESSAGE_EVENT, bolt_context)
    assert not bolt_context.say.called
    assert ExperimentSession.objects.count() == 0

    bolt_context["say"] = Mock()
    new_message(THREAD_REPLY_EVENT, bolt_context)

    assert not bolt_context.say.called
    assert ExperimentSession.objects.count() == 0


@pytest.mark.usefixtures("experiment_channel")
def test_response_to_mention_in_session_thread(bolt_context):
    bolt_context.client.chat_postMessage = Mock()
    new_message(BOT_MENTION_EVENT, bolt_context)

    bolt_context.client.chat_postMessage.assert_called_with(
        channel=BOT_MENTION_EVENT["channel"],
        text="new message",
        thread_ts=BOT_MENTION_EVENT["ts"],
    )
    assert ExperimentSession.objects.count() == 1

    bolt_context.client.chat_postMessage.reset_mock()
    thread_reply_with_mention = THREAD_REPLY_EVENT.copy()
    thread_reply_with_mention["text"] = f"<@{BOT_USER_ID}> thread reply with bot mention"

    new_message(thread_reply_with_mention, bolt_context)

    bolt_context.client.chat_postMessage.assert_called_with(
        channel=THREAD_REPLY_EVENT["channel"],
        text="thread reply with bot mention",
        thread_ts=THREAD_REPLY_EVENT["thread_ts"],
    )
    assert ExperimentSession.objects.count() == 1


@pytest.mark.usefixtures("experiment_channel")
def test_response_to_mention_in_non_session_thread(bolt_context):
    bolt_context.client.chat_postMessage = Mock()

    thread_reply_with_mention = THREAD_REPLY_EVENT.copy()
    thread_reply_with_mention["text"] = f"<@{BOT_USER_ID}> can you jump in here"

    new_message(thread_reply_with_mention, bolt_context)

    bolt_context.client.chat_postMessage.assert_called_once_with(
        channel=BOT_MENTION_EVENT["channel"],
        text="can you jump in here",
        thread_ts=BOT_MENTION_EVENT["ts"],
    )
    assert ExperimentSession.objects.count() == 1


@pytest.fixture()
def slack_install():
    return SlackInstallation.objects.create(
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
            "team_id": SLACK_TEAM_ID,
            "slack_install": slack_install,
            "bot_user_id": BOT_USER_ID,
            "say": Mock(),
        }
    )


@pytest.fixture()
def messaging_provider(team_with_users):
    return MessagingProviderFactory(type=MessagingProviderType.slack, config={"slack_team_id": SLACK_TEAM_ID})


@pytest.fixture()
def experiment_channel(experiment, messaging_provider):
    return ExperimentChannelFactory(
        experiment=experiment,
        platform=ChannelPlatform.SLACK,
        extra_data={"slack_channel_id": SLACK_CHANNEL_ID},
        messaging_provider=messaging_provider,
    )


@pytest.fixture()
def keyword_channel(experiment, messaging_provider):
    return ExperimentChannelFactory(
        experiment=experiment,
        platform=ChannelPlatform.SLACK,
        extra_data={"slack_channel_id": SLACK_ALL_CHANNELS, "keywords": ["health", "benefits"]},
        messaging_provider=messaging_provider,
    )


@pytest.fixture()
def default_channel(experiment, messaging_provider):
    return ExperimentChannelFactory(
        experiment=experiment,
        platform=ChannelPlatform.SLACK,
        extra_data={"slack_channel_id": SLACK_ALL_CHANNELS, "is_default": True},
        messaging_provider=messaging_provider,
    )


@pytest.mark.django_db()
@pytest.mark.usefixtures("keyword_channel")
def test_keyword_routing_matches_first_word(bolt_context):
    """Test that keyword matching works when keyword is first word"""
    bolt_context.client.chat_postMessage = MagicMock()

    # Should match "health" as first word after mention
    health_event = BOT_MENTION_EVENT.copy()
    health_event["text"] = f"<@{BOT_USER_ID}> health what are my options?"

    new_message(health_event, bolt_context)

    bolt_context.client.chat_postMessage.assert_called_once()
    assert ExperimentSession.objects.count() == 1


@pytest.mark.django_db()
def test_keyword_routing_case_insensitive(keyword_channel, bolt_context):
    """Test that keyword matching is case insensitive"""
    bolt_context.client.chat_postMessage = MagicMock()

    case_event = BOT_MENTION_EVENT.copy()
    case_event["text"] = f"<@{BOT_USER_ID}> HEALTH what about coverage?"

    new_message(case_event, bolt_context)

    bolt_context.client.chat_postMessage.assert_called_once()


@pytest.mark.django_db()
def test_channel_routing_priority(experiment_channel, keyword_channel, default_channel, bolt_context):
    """Test routing priority: specific channel > keywords > default"""
    bolt_context.client.chat_postMessage = MagicMock()

    # Message with keyword should go to specific channel (highest priority)
    priority_event = BOT_MENTION_EVENT.copy()
    # Start with the keyword to force a keyword match, then confirm specific-channel wins.
    priority_event["text"] = f"<@{BOT_USER_ID}> HEALTH I need assistance"

    new_message(priority_event, bolt_context)

    # Verify session was created with the specific channel
    assert ExperimentSession.objects.filter(experiment_channel=experiment_channel).exists()

    # Resend the same message to a different channel to test keyword match
    keyword_event = priority_event.copy()
    keyword_event["channel"] = "other_channel"
    new_message(keyword_event, bolt_context)

    assert ExperimentSession.objects.filter(experiment_channel=keyword_channel).exists()

    # Resend message without keyword to test default match
    default_event = keyword_event.copy()
    default_event["text"] = f"<@{BOT_USER_ID}> I need assistance"
    default_event["channel"] = "other_other_channel"
    new_message(default_event, bolt_context)

    assert ExperimentSession.objects.filter(experiment_channel=default_channel).exists()
