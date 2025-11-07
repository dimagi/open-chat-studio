from celery import shared_task
from django.core.management import call_command


@shared_task
def cleanup_silk_data():
    call_command("silk_request_garbage_collect")
