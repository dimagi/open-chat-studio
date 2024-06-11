"""
This module contains methods for handling event payloads from subscribed
events.

Manage event subscriptions at:

    https://api.slack.com/apps/<APP ID>/event-subscriptions
"""

import logging

from slack_bolt import BoltContext

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
    app.event({"type": "app_mention"})(app_mentioned)


def get_installation_for_context(context):
    try:
        return SlackInstallation.objects.get(
            enterprise_id=context.enterprise_id,
            slack_team_id=context.team_id,
        )
    except SlackInstallation.DoesNotExist:
        logger.error("Event received from unknown Slack installation: team_id=%s", context.team_id)
        return


def app_mentioned(slack_install, event, context: BoltContext):
    if not slack_install:
        slack_install = get_installation_for_context(context)
        if not slack_install:
            return

    context.ack()

    channel_id = event.get("channel")
    thread_ts = event.get("thread_ts", None) or event["ts"]

    experiment_channel = ExperimentChannel.objects.filter_extras(
        slack_install.team.slug, ChannelPlatform.SLACK, "slack_channel_id", channel_id
    ).first()
    if not experiment_channel:
        context.say("There are no bots associated with this channel.", thread_ts=thread_ts)
        return

    slack_user = event.get("user")

    try:
        session = ExperimentSession.objects.get(team=slack_install.team, external_id=thread_ts)
    except ExperimentSession.DoesNotExist:
        session = SlackChannel.start_new_session(
            experiment_channel.experiment, experiment_channel, slack_user, slack_thread_ts=thread_ts
        )

    # strip out the mention
    message_text = event["text"].replace(f"<{slack_install.bot_user_id}>", "")
    message = SlackMessage(thread_ts=thread_ts, message_text=message_text)

    ocs_channel = SlackChannel(experiment_channel, session, send_response_to_user=False)
    response = ocs_channel.new_user_message(message)
    context.say(response, thread_ts=thread_ts)
