"""
This module contains methods for handling event payloads from subscribed
events.

Manage event subscriptions at:

    https://api.slack.com/apps/<APP ID>/event-subscriptions
"""

import logging
import re

from slack_bolt import BoltContext, BoltResponse

from apps.channels.datamodels import SlackMessage
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import SlackChannel
from apps.experiments.models import ExperimentSession
from apps.slack.models import SlackInstallation
from apps.slack.slack_app import app

logger = logging.getLogger("slack.events")


def register_listeners():
    """Register these after DB setup is complete to avoid hitting the
    wrong DB e.g. during tests"""
    app.use(load_installation_and_team)
    app.event({"type": "message"})(new_message)


def new_message(event, context: BoltContext):
    thread_ts = event.get("thread_ts", None)
    is_bot_mention = context.bot_user_id in event.get("text", "")

    if is_bot_mention:
        respond_to_message(event, context)

    if thread_ts and (session := get_session_for_thread(context["team"], thread_ts)):
        respond_to_message(event, context, session)


def respond_to_message(event, context: BoltContext, session=None):
    context.ack("...")

    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts", None) or event["ts"]
    experiment_channel = ExperimentChannel.objects.filter_extras(
        context["team"].slug, ChannelPlatform.SLACK, "slack_channel_id", channel_id
    ).first()
    if not experiment_channel:
        context.say("There are no bots associated with this channel.", thread_ts=thread_ts)
        return

    slack_user = event.get("user")

    if not session:
        session = SlackChannel.start_new_session(
            experiment_channel.experiment, experiment_channel, slack_user, slack_thread_ts=thread_ts
        )

    # strip out the mention
    message_text = re.sub(rf"<@?{context.bot_user_id}>", "", event["text"])
    message = SlackMessage(thread_ts=thread_ts, message_text=message_text)

    ocs_channel = SlackChannel(experiment_channel, session, send_response_to_user=False)
    response = ocs_channel.new_user_message(message)
    context.say(response, thread_ts=thread_ts)


def load_installation_and_team(context: BoltContext, next):
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
    context["team"] = installation.team
    next()


def get_session_for_thread(team, thread_ts: str):
    try:
        return ExperimentSession.objects.select_related("team", "participant").get(team=team, external_id=thread_ts)
    except ExperimentSession.DoesNotExist:
        pass
