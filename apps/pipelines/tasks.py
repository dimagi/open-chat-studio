from celery.app import shared_task
from django.conf import settings
from django.core.mail import send_mail

from apps.pipelines.models import Pipeline


@shared_task(ignore_result=True)
def send_email_from_pipeline(recipient_list, subject, message):
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=False,
        html_message=message,
    )


@shared_task
def get_response_for_pipeline_test_message(pipeline_id: int, message_text: str, user_id: int):
    pipeline = Pipeline.objects.get(id=pipeline_id)
    return pipeline.simple_invoke(message_text, user_id=user_id)
