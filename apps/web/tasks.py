from celery import shared_task
from django.core.management import call_command

from apps.utils.celery import Queues


@shared_task(queue=Queues.BACKGROUND)
def cleanup_silk_data():
    call_command("silk_request_garbage_collect")
