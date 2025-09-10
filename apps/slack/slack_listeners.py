"""
This module contains methods for handling event payloads from subscribed
events.

Manage event subscriptions at:

    https://api.slack.com/apps/<APP ID>/event-subscriptions
"""

import logging
import re

from slack_bolt import BoltContext, BoltResponse

from apps.channels.const import SLACK_ALL_CHANNELS
from apps.channels.datamodels import SlackMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import SlackChannel
from apps.experiments.models import ExperimentSession
from apps.service_providers.messaging_service import SlackService
from apps.slack.exceptions import TeamAccessException
from apps.slack.models import SlackInstallation
from apps.slack.utils import make_session_external_id
from apps.teams.utils import current_team

logger = logging.getLogger("ocs.slack")


def new_message(event, context: BoltContext):
    thread_ts = event.get("thread_ts", None)
    channel_id = event.get("channel")

    session = None
    if thread_ts:
        session = get_session_for_thread(channel_id, thread_ts)

    is_bot_mention = context.bot_user_id in event.get("text", "")
    is_dm = _is_dm_channel(channel_id)
    if is_bot_mention or session or is_dm:
        respond_to_message(event, context, session)


def respond_to_message(event, context: BoltContext, session=None):
    context.ack("...")

    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts", None) or event["ts"]
    message_text = event.get("text", "")

    # For new sessions, use message text for keyword routing
    # For existing sessions, the experiment_channel is already determined
    if session:
        experiment_channel = session.experiment_channel
    else:
        experiment_channel = get_experiment_channel(channel_id, message_text, context)

    if not experiment_channel:
        context.say("Unable to find a bot to respond to your message.", thread_ts=thread_ts)
        return

    experiment = experiment_channel.experiment
    if session and session.team_id != experiment.team_id:
        raise TeamAccessException("Session and Channel teams do not match")

    with current_team(experiment.team):
        _respond_to_message(event, channel_id, thread_ts, experiment_channel, experiment, session, context)


def _respond_to_message(event, channel_id, thread_ts, experiment_channel, experiment, session, context):
    slack_user = event.get("user")

    if not session:
        external_id = make_session_external_id(channel_id, thread_ts)
        session = SlackChannel.start_new_session(
            working_experiment=experiment,
            experiment_channel=experiment_channel,
            participant_identifier=slack_user,
            session_external_id=external_id,
        )
    message_text = _strip_bot_mention(context, event["text"])
    message = SlackMessage(
        participant_id=slack_user, channel_id=channel_id, thread_ts=thread_ts, message_text=message_text
    )

    messaging_service = SlackService(slack_team_id="_", slack_installation_id=0)
    messaging_service.client = context.client
    ocs_channel = SlackChannel(
        experiment=experiment.default_version,
        experiment_channel=experiment_channel,
        experiment_session=session,
        messaging_service=messaging_service,
    )
    ocs_channel.new_user_message(message)


def _strip_bot_mention(context, text):
    """Remove the bot mention tag from the message"""
    return re.sub(rf"<@?{context.bot_user_id}>\s*", "", text)


def load_installation(context: BoltContext, next):
    """Middleware to handle loading of team etc."""
    try:
        installation = SlackInstallation.objects.get(
            enterprise_id=context.enterprise_id,
            slack_team_id=context.team_id,
        )
    except SlackInstallation.DoesNotExist:
        logger.error("Event received from unknown Slack installation: team_id=%s", context.team_id)
        return BoltResponse(status=200, body="")

    context["slack_install"] = installation
    next()


def get_session_for_thread(channel_id: str, thread_ts: str):
    external_id = make_session_external_id(channel_id, thread_ts)
    try:
        return ExperimentSession.objects.select_related("team", "participant", "experiment_channel").get(
            external_id=external_id
        )
    except ExperimentSession.DoesNotExist:
        pass


def get_experiment_channel(channel_id, message_text, bolt_context) -> ExperimentChannel | None:
    """Get the experiment channel for the given team and channel_id. This searches for exact matches
    on the channel ID and also for the special case of bots that are listening in all channels.
    For DM channels, it also supports keyword-based routing."""

    base_queryset = ExperimentChannel.objects.filter(platform=ChannelPlatform.SLACK, deleted=False).select_related(
        "experiment", "messaging_provider"
    )

    # Note: Workspace scoping would require filtering by encrypted config field
    # which is not supported. For now, we rely on proper messaging provider setup
    # to ensure channels are properly scoped to their workspaces.

    # First, try to find exact channel match (specific channel assignment)
    exact_channels_qs = base_queryset.filter(extra_data__contains={"slack_channel_id": channel_id})
    if exact_channel := exact_channels_qs.first():
        return exact_channel

    # If no exact match, check for "all channels" bots (including keyword-based routing)
    all_channels = base_queryset.filter(extra_data__contains={"slack_channel_id": SLACK_ALL_CHANNELS})

    # If we have message text, try keyword-based routing for all channels
    if message_text and (keyword := _get_keyword(bolt_context, message_text)):
        if keyword_match := all_channels.filter(extra_data__contains={"keywords": [keyword]}).first():
            return keyword_match

    return all_channels.filter(extra_data__is_default=True).first()


def _is_dm_channel(channel_id: str) -> bool:
    """Check if this is a DM channel (starts with 'D' in Slack)"""
    return channel_id.startswith("D")


def _get_keyword(context, message_text: str) -> str | None:
    """Extract the keyword from the message"""
    if not message_text:
        return None

    message_text = _strip_bot_mention(context, message_text)
    bot_mention_pattern = r"^([a-zA-Z0-9\-]+)"
    match = re.search(bot_mention_pattern, message_text)

    if not match:
        return None

    keyword = match.group(1).lower()
    return keyword if len(keyword) > 1 else None
