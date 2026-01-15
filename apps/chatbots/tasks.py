from celery import shared_task

from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing import TraceInfo


@shared_task
def send_bot_message(session_id: int, instruction_prompt: str):
    session = ExperimentSession.objects.get(id=session_id)
    session.ad_hoc_bot_message(
        instruction_prompt=instruction_prompt,
        trace_info=TraceInfo(name="Manual Session Start"),
    )
