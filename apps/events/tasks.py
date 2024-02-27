from celery.app import shared_task

from apps.events.models import TimeoutTrigger
from apps.experiments.models import ExperimentSession


@shared_task()
def enqueue_timed_out_events():
    active_triggers = TimeoutTrigger.objects.all()
    for trigger in active_triggers:
        for session in trigger.timed_out_sessions():
            fire_trigger.delay(trigger.id, session.id)


@shared_task
def fire_trigger(trigger_id, session_id):
    trigger = TimeoutTrigger.objects.prefetch_related("stats").get(id=trigger_id)
    session = ExperimentSession.objects.get(id=session_id)

    try:
        triggered = trigger.fire(session)
    except Exception:
        # TODO: add errors into stats model
        raise

    return triggered
