from celery.app import shared_task
from django.utils import timezone

from apps.files.models import File


@shared_task(ignore_result=True)
def clean_up_expired_files():
    """
    Cleans up expired files
    """
    File.objects.filter(expiry_date__lt=timezone.now()).delete()
