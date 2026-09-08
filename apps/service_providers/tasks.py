from celery.app import shared_task
from celery.utils.log import get_task_logger

from apps.service_providers.messaging_service import TemplateCheck, meta_error_message
from apps.service_providers.models import MessagingProvider, MessagingProviderType
from apps.utils.celery import Queues

log = get_task_logger("ocs.service_providers")


@shared_task(queue=Queues.BACKGROUND, ignore_result=True)
def sync_whatsapp_provider_task(provider_id: int):
    """Refresh everything cached about a Meta Cloud API provider: its numbers and its template.

    The two are fetched independently so a failure in one still leaves the other up to date.
    Failures are recorded on the provider rather than retried: the operator sees what Meta
    said and can refresh from the provider page.
    """
    provider = MessagingProvider.objects.filter(pk=provider_id, type=MessagingProviderType.meta_cloud_api).first()
    if not provider:
        return

    try:
        _sync_numbers(provider)
        _check_template(provider)
    finally:
        provider.mark_whatsapp_refresh_done()


def _sync_numbers(provider: MessagingProvider) -> None:
    try:
        provider.sync_whatsapp_numbers()
    except Exception as exc:  # noqa: BLE001 - the reason is shown to the operator, whatever it is
        log.exception("Failed to sync WhatsApp numbers for messaging provider %s", provider.pk)
        provider.mark_whatsapp_numbers_failed(meta_error_message(exc))


def _check_template(provider: MessagingProvider) -> None:
    try:
        provider.check_whatsapp_template()
    except Exception as exc:  # noqa: BLE001 - same: the panel reports it rather than staying blank
        log.exception("Failed to check the WhatsApp message template for messaging provider %s", provider.pk)
        provider.record_whatsapp_template_check(TemplateCheck(ok=False, error=meta_error_message(exc)))
