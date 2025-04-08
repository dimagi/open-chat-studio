"""
This module contains methods for handling event payloads from subscribed
events.

Manage event subscriptions at:

    https://api.slack.com/apps/<APP ID>/event-subscriptions
"""

import logging
import re

from django.db.models import Q
from slack_bolt import BoltContext, BoltResponse

from apps.channels.const import SLACK_ALL_CHANNELS
from apps.channels.datamodels import SlackMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import SlackChannel
from apps.experiments.models import ExperimentSession
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
    if is_bot_mention or session:
        respond_to_message(event, context, session)


def respond_to_message(event, context: BoltContext, session=None):
    context.ack("...")

    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts", None) or event["ts"]
    experiment_channel = get_experiment_channel(channel_id)

    if not experiment_channel:
        context.say("There are no bots associated with this channel.", thread_ts=thread_ts)
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

    # strip out the mention
    message_text = re.sub(rf"<@?{context.bot_user_id}>", "", event["text"])
    message = SlackMessage(
        participant_id=slack_user, channel_id=channel_id, thread_ts=thread_ts, message_text=message_text
    )

    # Set `send_response_to_user` to `False` to prevent it sending the message since we're going to send
    # it here using the already authenticated client.
    ocs_channel = SlackChannel(experiment.default_version, experiment_channel, session, send_response_to_user=False)
    response = ocs_channel.new_user_message(message).content
    context.say(response, thread_ts=thread_ts)


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


def get_experiment_channel(channel_id) -> ExperimentChannel | None:
    """Get the experiment channel for the given team and channel_id. This searches for exact matches
    on the channel ID and also for the special case of bots that are listening in all channels."""
    channel_filter = Q(extra_data__contains={"slack_channel_id": channel_id}) | Q(
        extra_data__contains={"slack_channel_id": SLACK_ALL_CHANNELS}
    )
    channels = (
        ExperimentChannel.objects.filter(channel_filter)
        .filter(platform=ChannelPlatform.SLACK)
        .select_related("experiment", "messaging_provider")
        .all()
    )
    if not channels:
        return

    if len(channels) == 1:
        return channels[0]

    # if there are multiple channels, we need to find the one that matches the channel_id
    return [channel for channel in channels if channel.extra_data.get("slack_channel_id") == channel_id][0]
