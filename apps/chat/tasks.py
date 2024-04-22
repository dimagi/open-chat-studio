import logging
from uuid import UUID

from celery.app import shared_task
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Count, F, OuterRef, Q, Subquery, functions
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.chat.bots import TopicBot
from apps.chat.channels import ChannelBase
from apps.chat.models import ChatMessage, ChatMessageType, ScheduledMessage
from apps.experiments.models import ExperimentSession, Participant, SessionStatus
from apps.utils.django_db import MakeInterval
from apps.web.meta import absolute_url

logger = logging.getLogger(__name__)

STATUSES_FOR_COMPLETE_CHATS = [SessionStatus.PENDING_REVIEW, SessionStatus.COMPLETE, SessionStatus.UNKNOWN]


def _get_latest_sessions_for_participants(
    participant_chat_ids: list, experiment_public_id: UUID
) -> list[ExperimentSession]:
    latest_session_id = (
        ExperimentSession.objects.filter(experiment__public_id=experiment_public_id, participant=OuterRef("pk"))
        .order_by("-created_at")
        .values("id")[:1]
    )

    latest_participant_session_ids = (
        Participant.objects.filter(
            experimentsession__experiment__public_id=experiment_public_id, external_chat_id__in=participant_chat_ids
        )
        .annotate(latest_session_id=Subquery(latest_session_id))
        .values("latest_session_id")
    )

    return (
        ExperimentSession.objects.filter(id__in=Subquery(latest_participant_session_ids))
        .prefetch_related("experiment")
        .all()
    )


@shared_task
def send_bot_message_to_users(message: str, chat_ids: list[str], is_bot_instruction: bool, experiment_public_id: UUID):
    """This sends `message` to the sessions related to `chat_ids` as the bot.

    If `is_bot_instruction` is true, the message will be interpreted as an instruction for the bot. For each
    chat_id in `chat_ids`, the bot will be given the instruction along with the chat history. Only the bot's
    response will be saved to the chat history.
    """

    latest_sessions = _get_latest_sessions_for_participants(chat_ids, experiment_public_id=UUID(experiment_public_id))

    for experiment_session in latest_sessions:
        try:
            if experiment_session.is_stale():
                continue

            bot_message_to_user = message
            if is_bot_instruction:
                bot_message_to_user = bot_prompt_for_user(
                    experiment_session=experiment_session, prompt_instruction=message
                )
            else:
                ChatMessage.objects.create(
                    chat=experiment_session.chat, message_type=ChatMessageType.AI, content=message
                )
            try_send_message(experiment_session=experiment_session, message=bot_message_to_user)
        except Exception as exception:
            logger.exception(exception)


@shared_task(bind=True)
def no_activity_pings(self):
    key = f"no_activity_pings{self.app.oid}"
    lock = cache.lock(key, timeout=60 * 5)
    if lock.acquire(blocking=False):
        try:
            _no_activity_pings()
        finally:
            lock.release()
    else:
        logger.warning("Unable to acquire lock for no_activity_pings")


def _no_activity_pings():
    experiment_sessions_to_ping = _get_sessions_to_ping()

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
        ping_message = bot_prompt_for_user(experiment_session, prompt_instruction=bot_ping_message)
        try:
            # TODO: experiment_session.send_bot_message
            try_send_message(experiment_session=experiment_session, message=ping_message)
        finally:
            experiment_session.no_activity_ping_count += 1
            experiment_session.save(update_fields=["no_activity_ping_count"])


def _get_sessions_to_ping():
    """
    Criteria:
    1. The user have communicated with the bot
    2. The experiment session is not considered "complete"
    3. The experiment_session has a "no activity config" and the number of pings already received is smaller than
    the max number of pings defined in the config
    4. The last message in a session was from a bot and was created more than <user defined> minutes ago
    """

    chats_with_human_msgs = (
        ChatMessage.objects.annotate(human_msgs=Count("id", filter=Q(message_type=ChatMessageType.HUMAN)))
        .filter(human_msgs__gt=0)
        .values("chat_id")
    )

    max_pings = F("chat__experiment_session__experiment__no_activity_config__max_pings")
    last_messages = (
        ChatMessage.objects.filter(chat_id__in=chats_with_human_msgs)
        .exclude(chat__experiment_session__status__in=STATUSES_FOR_COMPLETE_CHATS)
        .exclude(chat__experiment_session__experiment__no_activity_config=None)
        .exclude(chat__experiment_session__no_activity_ping_count__gte=max_pings)
        .order_by("chat_id", "-created_at")
        .distinct("chat_id")
    )

    ping_after = MakeInterval("secs", F("chat__experiment_session__experiment__no_activity_config__ping_after"))
    matches = (
        ChatMessage.objects.filter(id__in=last_messages)
        .filter(message_type=ChatMessageType.AI, created_at__lt=functions.Now() - ping_after)
        .select_related(
            "chat__experiment_session",
            "chat__experiment_session__experiment_channel",
            "chat__experiment_session__experiment",
            "chat__experiment_session__experiment__no_activity_config",
        )
    )

    return [chat_msg.chat.experiment_session for chat_msg in matches]


def bot_prompt_for_user(experiment_session: ExperimentSession, prompt_instruction: str) -> str:
    """Sends the `prompt_instruction` along with the chat history to the LLM to formulate an appropriate prompt
    message. The response from the bot will be saved to the chat history.
    """
    topic_bot = TopicBot(experiment_session)
    return topic_bot.process_input(user_input=prompt_instruction, save_input_to_history=False)


def try_send_message(experiment_session: ExperimentSession, message: str, fail_silently=True):
    """Tries to send a message to the experiment session"""
    try:
        channel = ChannelBase.from_experiment_session(experiment_session)
        channel.new_bot_message(message)
    except Exception as e:
        logging.exception(f"Could not send message to experiment session {experiment_session.id}. Reason: {e}")
        if not fail_silently:
            raise e


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


def _get_messages_to_fire():
    return ScheduledMessage.objects.filter(resolved=False, next_trigger_date__lte=functions.Now())


@shared_task()
def poll_scheduled_messages():
    """Polls scheduled messages and triggers those that are due. After triggering, it updates the database with the
    new trigger details for each message."""

    messages = _get_messages_to_fire()
    for message in messages:
        message.safe_trigger()

    # Hmm, reason about this update. Should it not be in the trigger method?
    ScheduledMessage.objects.bulk_update(
        messages, ["total_triggers", "resolved", "next_trigger_date", "last_triggered_at"]
    )
