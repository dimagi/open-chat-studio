from datetime import timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from django.db.models import Q
from django.utils import timezone

from apps.experiments.models import ExperimentSession, SessionStatus
from apps.service_providers.tracing import TraceInfo
from apps.utils.celery import Queues

log = get_task_logger("ocs.chatbots")


@shared_task(queue=Queues.CHAT)
def send_bot_message(session_id: int, instruction_prompt: str):
    session = ExperimentSession.objects.get(id=session_id)
    session.ad_hoc_bot_message(
        instruction_prompt=instruction_prompt,
        trace_info=TraceInfo(name="Manual Session Start"),
    )


@shared_task(queue=Queues.BACKGROUND)
def send_broadcast_message(experiment_id: int, channel_ids: list[int], message: str, active_within_days: int):
    """Deliver `message` to the recently active participants of this chatbot on the given channels.

    Runs on the background queue and fans out one send per session, so a broadcast to a
    large audience can't hold up the latency-sensitive chat queue while it walks the list.
    """
    session_ids = get_broadcast_session_ids(experiment_id, channel_ids, active_within_days)
    log.info(
        "Broadcasting to %s session(s) active in the last %s day(s) for experiment %s",
        len(session_ids),
        active_within_days,
        experiment_id,
    )
    for session_id in session_ids:
        send_broadcast_message_to_session.delay(session_id=session_id, message=message)


def get_broadcast_session_ids(experiment_id: int, channel_ids: list[int], active_within_days: int) -> list[int]:
    """The most recent active session per (participant, channel) for a broadcast.

    A participant is reachable only through a session they already have -- that is what holds
    the address to deliver to -- and only their latest session on a channel is the live
    conversation, so older ones are skipped. Someone on two channels is messaged on both.

    Only `ACTIVE` sessions count. A session resting at `SETUP` or `PENDING` has no conversation
    in it yet, and one that has been ended sits at `PENDING_REVIEW` or `COMPLETE` -- broadcasting
    into either would push a message at someone who is not in a conversation with the bot.

    `active_within_days` is the audience cutoff the sender chose in the broadcast dialog: a
    participant who has been quiet for longer than that is left alone rather than pulled back
    into a conversation they walked away from weeks ago. Activity is measured the way the
    sessions table and its "Last Activity" filter measure it -- `last_activity_at`, falling
    back to `created_at` for a session that never received a participant message.

    Both filters run before the newest-per-group pick, so a participant whose newest session
    has been ended, or has gone quiet, is still reached on an older session that qualifies.
    """
    cutoff = timezone.now() - timedelta(days=active_within_days)
    return list(
        ExperimentSession.objects.filter(
            experiment_id=experiment_id,
            experiment_channel_id__in=channel_ids,
            status=SessionStatus.ACTIVE,
        )
        .filter(Q(last_activity_at__gte=cutoff) | Q(last_activity_at__isnull=True, created_at__gte=cutoff))
        # `-id` breaks ties on `created_at` so the newest session is picked deterministically.
        .order_by("participant_id", "experiment_channel_id", "-created_at", "-id")
        .distinct("participant_id", "experiment_channel_id")
        .values_list("id", flat=True)
    )


@shared_task(queue=Queues.CHAT)
def send_broadcast_message_to_session(session_id: int, message: str):
    """Deliver one broadcast message verbatim, bypassing the LLM.

    `ad_hoc_bot_message` swallows delivery failures and skips disabled channels, so one
    unreachable participant never aborts the rest of the broadcast.
    """
    session = ExperimentSession.objects.get(id=session_id)
    session.ad_hoc_bot_message(
        instruction_prompt=None,
        message_text=message,
        trace_info=TraceInfo(name="Broadcast Message"),
    )
