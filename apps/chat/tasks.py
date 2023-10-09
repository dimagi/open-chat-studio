import logging
from datetime import datetime, timedelta
from typing import List, Optional

import pytz
from celery.app import shared_task
from django.db.models import OuterRef, Subquery

from apps.channels.models import ChannelSession
from apps.chat.bots import get_bot_from_experiment
from apps.chat.exceptions import ExperimentChannelRepurposedException
from apps.chat.message_handlers import MessageHandler
from apps.chat.models import Chat, ChatMessage, FutureMessage
from apps.chat.task_utils import isolate_task, redis_task_lock
from apps.experiments.models import ExperimentSession, SessionStatus

STATUSES_FOR_COMPLETE_CHATS = [SessionStatus.PENDING_REVIEW, SessionStatus.COMPLETE, SessionStatus.UNKNOWN]


@shared_task(bind=True)
def periodic_tasks(self):
    lock_id = self.name
    with redis_task_lock(lock_id, self.app.oid) as acquired:
        if not acquired:
            return
        _no_activity_pings()
        _check_future_messages()


@shared_task
def send_bot_message_to_users(message: str, chat_ids: List[str], is_bot_instruction: Optional[bool]):
    """This sends `message` to the sessions related to `chat_ids` as the bot.

    If `is_bot_instruction` is true, the message will be interpreted as an instruction for the bot. For each
    chat_id in `chat_ids`, the bot will be given the instruction along with the chat history. Only the bot's
    response will be saved to the chat history.
    """
    print(f"is_bot_instruction: {is_bot_instruction}")
    channel_sessions = (
        ChannelSession.objects.filter(external_chat_id__in=chat_ids)
        .prefetch_related("experiment_session__experiment", "experiment_session__experiment")
        .all()
    )
    for channel_session in channel_sessions:
        if channel_session.is_stale():
            continue

        experiment_session = channel_session.experiment_session
        bot_message_to_user = message
        if is_bot_instruction:
            bot_message_to_user = _bot_prompt_for_user(
                experiment_session=experiment_session, prompt_instruction=message
            )
        else:
            ChatMessage.objects.create(chat=experiment_session.chat, message_type="ai", content=message)
        _try_send_message(experiment_session=experiment_session, message=bot_message_to_user)


@isolate_task
def _no_activity_pings():
    """
    Criteria:
    1. The user have communicated with the bot
    2. The experiment session is not considered "complete"
    3. The experiment_session has a "no activity config" and the number of pings already received is smaller than
    the max number of pings defined in the config
    4. The last message in a session was from a bot and was created more than <user defined> minutes ago
    """

    UTC = pytz.timezone("UTC")
    now = datetime.now().astimezone(UTC)
    experiment_sessions_to_ping: List[ExperimentSession] = []

    subquery = ChatMessage.objects.filter(chat=OuterRef("pk"), message_type="human").values("chat_id")
    # Why not exclude the SETUP status? "Normal" UI chats have a SETUP status
    chats = (
        Chat.objects.filter(pk__in=Subquery(subquery))
        .exclude(experiment_session__status__in=STATUSES_FOR_COMPLETE_CHATS)
        .exclude(experiment_session__experiment__no_activity_config=None)
        .select_related("experiment_session")
        .all()
    )

    for chat in chats:
        latest_message = ChatMessage.objects.filter(chat=chat).order_by("-created_at").first()
        if latest_message and latest_message.message_type == "ai":
            experiment_session: ExperimentSession = chat.experiment_session
            no_activity_config = experiment_session.experiment.no_activity_config
            max_pings_reached = experiment_session.no_activity_ping_count >= no_activity_config.max_pings
            message_created_at = latest_message.created_at.astimezone(UTC)
            max_time_elapsed = message_created_at < now - timedelta(minutes=no_activity_config.ping_after)
            if not max_pings_reached and max_time_elapsed:
                experiment_sessions_to_ping.append(experiment_session)

    for experiment_session in experiment_sessions_to_ping:
        bot_ping_message = experiment_session.experiment.no_activity_config.message_for_bot
        ping_message = _bot_prompt_for_user(experiment_session, bot_ping_message=bot_ping_message)
        try:
            _try_send_message(experiment_session=experiment_session, message=ping_message)
        finally:
            experiment_session.no_activity_ping_count += 1
            experiment_session.save()


def _bot_prompt_for_user(experiment_session: ExperimentSession, prompt_instruction: str) -> str:
    """Sends the `prompt_instruction` along with the chat history to the LLM to formulate an appropriate prompt
    message. The response from the bot will be saved to the chat history.
    """
    topic_bot = get_bot_from_experiment(experiment_session.experiment, experiment_session.chat)
    bot_prompt = topic_bot.get_response(user_input=prompt_instruction, is_prompt_instruction=True)
    topic_bot.save_history()
    return bot_prompt


def _try_send_message(experiment_session: ExperimentSession, message: str):
    """Tries to send a message to the experiment session"""
    try:
        channel_session = experiment_session.get_channel_session()
        experiment_channel = channel_session.experiment_channel
        if experiment_channel.experiment != experiment_session.experiment:
            # The experiment channel's experiment might have changed
            raise ExperimentChannelRepurposedException(
                message=f"ExperimentChannel is pointing to experiment '{experiment_channel.experiment.name}' whereas the current experiment session points to experiment '{experiment_session.experiment.name}'"
            )

        handler = MessageHandler.from_experiment_session(experiment_session)
        handler.new_bot_message(message)
    except ExperimentChannelRepurposedException as e:
        raise e
    except Exception as e:
        logging.error(f"Could not send message to experiment session {experiment_session.id}. Reason: {e}")


@isolate_task
def _check_future_messages():
    """Checks to see if there's any FutureMessages that need to be sent. After sending it, it schedules the
    next one, depending on the configuration
    """
    utc = pytz.timezone("UTC")
    utc_now = datetime.now().replace(second=0, microsecond=0).astimezone(utc)
    one_min_from_now = utc_now + timedelta(minutes=1)
    messages = FutureMessage.objects.filter(
        resolved=False,
        due_at__lte=one_min_from_now,
    ).all()
    for message in messages:
        try:
            experiment_session = message.experiment_session
            ChatMessage.objects.create(chat=experiment_session.chat, message_type="ai", content=message.message)
            _try_send_message(experiment_session=experiment_session, message=message.message)
            was_last_in_series = message.end_date.replace(second=0, microsecond=0) <= utc_now
            if was_last_in_series:
                message.resolved = True
            else:
                _update_future_message_due_at(message)
        except ExperimentChannelRepurposedException:
            logging.info(f"Resolving message {message.id} due to repurposed experiment channel")
            message.resolved = True
        message.save()


def _update_future_message_due_at(future_message: FutureMessage):
    """Adjusts the due time of a future message based on its interval and missed periods.

    This function calculates the next due time for a future message based on its interval
    and the number of missed intervals since its original due time. If the future message's
    interval_minutes is not set, no adjustments are made.
    """
    if not future_message.interval_minutes:
        return
    utc = pytz.timezone("UTC")
    now_in_utc = datetime.now().astimezone(utc)
    due_at_in_utc = future_message.due_at.astimezone(utc)
    # The due_at is not represented as UTC so we can do UTC operation on it
    minutes_missed = (now_in_utc - due_at_in_utc).total_seconds() / 60
    periods_missed = minutes_missed // future_message.interval_minutes
    next_due_at = future_message.due_at + timedelta(minutes=future_message.interval_minutes) * (periods_missed + 1)
    future_message.due_at = next_due_at
