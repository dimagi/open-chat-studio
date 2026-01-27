from celery.app import shared_task
from celery.utils.log import get_task_logger

from apps.events.models import ScheduledMessage, StaticTrigger, StaticTriggerType, TimeoutTrigger
from apps.experiments.models import ExperimentSession

logger = get_task_logger("ocs.events")


@shared_task(ignore_result=True)
def enqueue_static_triggers(session_id, trigger_type):
    trigger_ids = _get_static_triggers_to_fire(session_id, trigger_type)
    for trigger_id in trigger_ids:
        fire_static_trigger.delay(trigger_id, session_id)


def _get_static_triggers_to_fire(session_id: int, trigger_type: StaticTrigger):
    session = ExperimentSession.objects.get(id=session_id)
    experiment_version = session.experiment_version

    trigger_types_to_filter = [trigger_type]
    if trigger_type in StaticTriggerType.end_conversation_types():
        # CONVERSATION_END is never raised directly, but it needs to trigger on all end conversation types
        trigger_types_to_filter.append(StaticTriggerType.CONVERSATION_END)

    queryset = StaticTrigger.objects.filter(
        experiment=experiment_version, type__in=trigger_types_to_filter, is_active=True
    )

    return queryset.values_list("id", flat=True)


@shared_task(ignore_result=True)
def fire_static_trigger(trigger_id, session_id):
    trigger = StaticTrigger.objects.get(id=trigger_id)
    session = ExperimentSession.objects.get(id=session_id)
    triggered = trigger.fire(session)
    return triggered


@shared_task(ignore_result=True)
def enqueue_timed_out_events():
    active_triggers = TimeoutTrigger.objects.published_versions().filter(is_active=True)
    for trigger in active_triggers:
        for session in trigger.timed_out_sessions():
            if session.is_stale():
                logger.warning(
                    f"ExperimentChannel is pointing to experiment '{session.experiment.name}'"
                    "whereas the current experiment session points to experiment"
                    f"'{session.experiment.name}'"
                )
                continue
            else:
                fire_trigger.delay(trigger.id, session.id)


@shared_task(ignore_result=True)
def fire_trigger(trigger_id, session_id):
    trigger = TimeoutTrigger.objects.get(id=trigger_id)
    session = ExperimentSession.objects.get(id=session_id)
    triggered = trigger.fire(session)
    return triggered


@shared_task(ignore_result=True)
def poll_scheduled_messages():
    """Polls scheduled messages and triggers those that are due. After triggering, it updates the database with the
    new trigger details for each message."""
    messages = ScheduledMessage.objects.get_messages_to_fire()
    for message in messages:
        message.safe_trigger()


@shared_task(ignore_result=True)
def retry_scheduled_message(scheduled_message_id: int, attempt_number: int):
    try:
        message = ScheduledMessage.objects.get(id=scheduled_message_id)
        message.safe_trigger(attempt_number=attempt_number)
    except ScheduledMessage.DoesNotExist:
        logger.warning(f"ScheduledMessage with id={scheduled_message_id} not found for retry.")
