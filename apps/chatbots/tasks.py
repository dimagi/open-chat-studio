from celery import shared_task
from celery.utils.log import get_task_logger

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
def send_broadcast_message(experiment_id: int, channel_ids: list[int], message: str):
    """Deliver `message` to every participant of this chatbot on the given channels.

    Runs on the background queue and fans out one send per session, so a broadcast to a
    large audience can't hold up the latency-sensitive chat queue while it walks the list.
    """
    session_ids = get_broadcast_session_ids(experiment_id, channel_ids)
    log.info("Broadcasting to %s session(s) for experiment %s", len(session_ids), experiment_id)
    for session_id in session_ids:
        send_broadcast_message_to_session.delay(session_id=session_id, message=message)


def get_broadcast_session_ids(experiment_id: int, channel_ids: list[int]) -> list[int]:
    """The most recent active session per (participant, channel) for a broadcast.

    A participant is reachable only through a session they already have -- that is what holds
    the address to deliver to -- and only their latest session on a channel is the live
    conversation, so older ones are skipped. Someone on two channels is messaged on both.

    Only `ACTIVE` sessions count. A session resting at `SETUP` or `PENDING` has no conversation
    in it yet, and one that has been ended sits at `PENDING_REVIEW` or `COMPLETE` -- broadcasting
    into either would push a message at someone who is not in a conversation with the bot. The
    status is filtered before the newest-per-group pick, so a participant whose newest session
    has been ended is still reached on an older one that is still active.
    """
    return list(
        ExperimentSession.objects.filter(
            experiment_id=experiment_id,
            experiment_channel_id__in=channel_ids,
            status=SessionStatus.ACTIVE,
        )
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
