from celery.app import shared_task
from django.db import models

from apps.events.models import TimeoutTrigger
from apps.experiments.models import ExperimentSession


def trigger_timed_out_events():
    active_triggers = TimeoutTrigger.objects.filter(trigger_count__lt=models.F("total_num_triggers"))
    for trigger in active_triggers:
        for session in trigger.timed_out_sessions():
            fire_trigger.delay(trigger.id, session.id)


@shared_task
def fire_trigger(trigger_id, session_id):
    trigger = TimeoutTrigger.objects.get(id=trigger_id)
    session = ExperimentSession.objects.get(id=session_id)
    return trigger.fire(session)
