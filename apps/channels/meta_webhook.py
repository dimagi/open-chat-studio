import hashlib
import hmac

from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.crypto import constant_time_compare

from apps.service_providers.models import MessagingProvider, MessagingProviderType


def extract_message_values(data: dict) -> list[dict]:
    """Extract value dicts that contain messages from Meta webhook payload.

    See https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint/

    See https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview#fields for an
    example of the payload
    """

    values = []
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if value.get("messages") and value.get("metadata", {}).get("phone_number_id"):
                values.append(value)
    return values


def verify_webhook(request) -> HttpResponse:
    """Handle the Meta webhook GET verification handshake."""
    mode = request.GET.get("hub.mode")
    token = request.GET.get("hub.verify_token")
    challenge = request.GET.get("hub.challenge")

    if mode != "subscribe" or not token or not challenge:
        return HttpResponseBadRequest("Verification failed.")

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    exists = MessagingProvider.objects.filter(
        type=MessagingProviderType.meta_cloud_api,
        extra_data__verify_token_hash=token_hash,
    ).exists()

    if exists:
        return HttpResponse(challenge, content_type="text/plain")

    return HttpResponseBadRequest("Verification failed.")


def verify_signature(payload: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify the X-Hub-Signature-256 header from Meta webhooks."""
    if not signature_header.startswith("sha256=") or not app_secret:
        return False

    expected_signature = signature_header[7:]
    computed = hmac.new(
        app_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return constant_time_compare(computed, expected_signature)
