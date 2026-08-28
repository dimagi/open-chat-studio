from celery.app import shared_task
from celery.utils.log import get_task_logger

from apps.service_providers.messaging_service import meta_error_message
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.utils.celery import Queues

log = get_task_logger("ocs.service_providers")


@shared_task(queue=Queues.BACKGROUND, ignore_result=True)
def sync_whatsapp_numbers_task(provider_id: int):
    """Cache the WhatsApp numbers on a Meta Cloud API provider, each with its phone number ID.

    Failures are recorded on the provider rather than retried: the operator sees what Meta
    said and can retry from the provider page.
    """
    provider = MessagingProvider.objects.filter(pk=provider_id, type=MessagingProviderType.meta_cloud_api).first()
    if not provider:
        return
    try:
        provider.sync_whatsapp_numbers()
    except Exception as exc:  # noqa: BLE001 - the reason is shown to the operator, whatever it is
        log.exception("Failed to sync WhatsApp numbers for messaging provider %s", provider_id)
        provider.mark_whatsapp_numbers_failed(meta_error_message(exc))
