import logging

from celery.app import shared_task

from apps.events.models import ScheduledMessage, StaticTrigger, TimeoutTrigger
from apps.experiments.models import ExperimentSession

logger = logging.getLogger("ocs.events")


@shared_task(ignore_result=True)
def enqueue_static_triggers(session_id, trigger_type):
    trigger_ids = _get_static_triggers_to_fire(session_id, trigger_type)
    for trigger_id in trigger_ids:
        fire_static_trigger.delay(trigger_id, session_id)


def _get_static_triggers_to_fire(session_id, trigger_type):
    session = ExperimentSession.objects.get(id=session_id)
    experiment_version = session.experiment_version
    trigger_ids = StaticTrigger.objects.filter(experiment=experiment_version, type=trigger_type).values_list(
        "id", flat=True
    )
    return trigger_ids


@shared_task(ignore_result=True)
def fire_static_trigger(trigger_id, session_id):
    trigger = StaticTrigger.objects.get(id=trigger_id)
    session = ExperimentSession.objects.get(id=session_id)
    triggered = trigger.fire(session)
    return triggered


@shared_task(ignore_result=True)
def enqueue_timed_out_events():
    active_triggers = TimeoutTrigger.objects.published_versions().all()
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
