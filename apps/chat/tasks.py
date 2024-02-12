import logging
from datetime import datetime, timedelta
from uuid import UUID

import pytz
from celery.app import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import OuterRef, Subquery
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.chat.bots import TopicBot
from apps.chat.channels import ChannelBase
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.chat.task_utils import isolate_task, redis_task_lock
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.web.meta import absolute_url

logger = logging.getLogger(__name__)

STATUSES_FOR_COMPLETE_CHATS = [SessionStatus.PENDING_REVIEW, SessionStatus.COMPLETE, SessionStatus.UNKNOWN]


@shared_task(bind=True)
def periodic_tasks(self):
    lock_id = self.name
    with redis_task_lock(lock_id, self.app.oid) as acquired:
        if not acquired:
            return
        _no_activity_pings()


@shared_task
def send_bot_message_to_users(message: str, chat_ids: list[str], is_bot_instruction: bool, experiment_public_id: UUID):
    """This sends `message` to the sessions related to `chat_ids` as the bot.

    If `is_bot_instruction` is true, the message will be interpreted as an instruction for the bot. For each
    chat_id in `chat_ids`, the bot will be given the instruction along with the chat history. Only the bot's
    response will be saved to the chat history.
    """
    experiment_sessions = (
        ExperimentSession.objects.filter(
            external_chat_id__in=chat_ids, experiment__public_id=UUID(experiment_public_id)
        )
        .prefetch_related(
            "experiment",
        )
        .all()
    )
    for experiment_session in experiment_sessions:
        try:
            if experiment_session.is_stale():
                continue

            bot_message_to_user = message
            if is_bot_instruction:
                bot_message_to_user = _bot_prompt_for_user(
                    experiment_session=experiment_session, prompt_instruction=message
                )
            else:
                ChatMessage.objects.create(
                    chat=experiment_session.chat, message_type=ChatMessageType.AI, content=message
                )
            _try_send_message(experiment_session=experiment_session, message=bot_message_to_user)
        except Exception as exception:
            logger.exception(exception)


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
    experiment_sessions_to_ping: list[ExperimentSession] = []

    subquery = ChatMessage.objects.filter(chat=OuterRef("pk"), message_type=ChatMessageType.HUMAN).values("chat_id")
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
        if latest_message and latest_message.message_type == ChatMessageType.AI:
            experiment_session: ExperimentSession = chat.experiment_session
            no_activity_config = experiment_session.experiment.no_activity_config
            max_pings_reached = experiment_session.no_activity_ping_count >= no_activity_config.max_pings
            message_created_at = latest_message.created_at.astimezone(UTC)
            max_time_elapsed = message_created_at < now - timedelta(minutes=no_activity_config.ping_after)
            if not max_pings_reached and max_time_elapsed:
                experiment_sessions_to_ping.append(experiment_session)

    for experiment_session in experiment_sessions_to_ping:
        bot_ping_message = experiment_session.experiment.no_activity_config.message_for_bot

        experiment_channel = experiment_session.experiment_channel
        if experiment_session.is_stale():
            # The experiment channel's experiment might have changed
            logger.warning(
                f"ExperimentChannel is pointing to experiment '{experiment_channel.experiment.name}'"
                "whereas the current experiment session points to experiment"
                f"'{experiment_session.experiment.name}'"
            )
            return
        ping_message = _bot_prompt_for_user(experiment_session, prompt_instruction=bot_ping_message)
        try:
            _try_send_message(experiment_session=experiment_session, message=ping_message)
        finally:
            experiment_session.no_activity_ping_count += 1
            experiment_session.save()


def _bot_prompt_for_user(experiment_session: ExperimentSession, prompt_instruction: str) -> str:
    """Sends the `prompt_instruction` along with the chat history to the LLM to formulate an appropriate prompt
    message. The response from the bot will be saved to the chat history.
    """
    topic_bot = TopicBot(experiment_session)
    return topic_bot.process_input(user_input=prompt_instruction, save_input_to_history=False)


def _try_send_message(experiment_session: ExperimentSession, message: str):
    """Tries to send a message to the experiment session"""
    try:
        handler = ChannelBase.from_experiment_session(experiment_session)
        handler.new_bot_message(message)
    except Exception as e:
        logging.error(f"Could not send message to experiment session {experiment_session.id}. Reason: {e}")


@shared_task
def notify_users_of_safety_violations_task(experiment_session_id: int, safety_layer_id: int):
    experiment_session = ExperimentSession.objects.get(id=experiment_session_id)
    experiment = experiment_session.experiment
    if not experiment.safety_violation_notification_emails:
        return

    email_context = {
        "session_link": absolute_url(
            reverse(
                "experiments:experiment_session_view",
                kwargs={
                    "session_id": experiment_session.public_id,
                    "experiment_id": experiment.public_id,
                    "team_slug": experiment.team.slug,
                },
            )
        ),
        "safety_layer_link": absolute_url(
            reverse("experiments:safety_edit", kwargs={"pk": safety_layer_id, "team_slug": experiment.team.slug})
        ),
    }
    send_mail(
        subject=_("A Safety Layer was breached"),
        message=render_to_string("experiments/email/safety_violation.txt", context=email_context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=experiment.safety_violation_notification_emails,
        fail_silently=False,
        html_message=render_to_string("experiments/email/safety_violation.html", context=email_context),
    )
