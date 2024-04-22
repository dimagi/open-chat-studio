import logging

from celery.app import shared_task

from apps.events.models import StaticTrigger, TimeoutTrigger
from apps.experiments.models import ExperimentSession

logger = logging.getLogger(__name__)


@shared_task
def enqueue_static_triggers(session_id, trigger_type):
    session = ExperimentSession.objects.get(id=session_id)

    trigger_ids = StaticTrigger.objects.filter(experiment_id=session.experiment_id, type=trigger_type).values_list(
        "id", flat=True
    )
    for trigger_id in trigger_ids:
        fire_static_trigger.delay(trigger_id, session_id)


@shared_task
def fire_static_trigger(trigger_id, session_id):
    trigger = StaticTrigger.objects.get(id=trigger_id)
    session = ExperimentSession.objects.get(id=session_id)
    triggered = trigger.fire(session)
    return triggered


@shared_task
def enqueue_timed_out_events():
    active_triggers = TimeoutTrigger.objects.all()
    for trigger in active_triggers:
        for session in trigger.timed_out_sessions():
            if session.is_stale():
                logger.warning(
                    f"ExperimentChannel is pointing to experiment '{session.experiment_channel.experiment.name}'"
                    "whereas the current experiment session points to experiment"
                    f"'{session.experiment.name}'"
                )
                continue
            else:
                fire_trigger.delay(trigger.id, session.id)


@shared_task
def fire_trigger(trigger_id, session_id):
    trigger = TimeoutTrigger.objects.get(id=trigger_id)
    session = ExperimentSession.objects.get(id=session_id)
    triggered = trigger.fire(session)
    return triggered
