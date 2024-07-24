from celery.app import shared_task
from django.conf import settings
from django.core.mail import send_mail


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
