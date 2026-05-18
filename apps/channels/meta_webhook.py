import hashlib
import hmac
from typing import NotRequired, TypedDict, cast

from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.crypto import constant_time_compare

from apps.service_providers.models import MessagingProvider, MessagingProviderType

# `from` is a Python keyword, so functional TypedDict syntax is required to express it as a field.
MetaCloudAPIWebhookMessage = TypedDict(
    "MetaCloudAPIWebhookMessage",
    {
        "id": str,
        "timestamp": str,
        "type": str,
        "from": NotRequired[str],
        "from_user_id": str,
        "text": NotRequired[dict],
        "audio": NotRequired[dict],
        "voice": NotRequired[dict],
        "image": NotRequired[dict],
        "video": NotRequired[dict],
        "document": NotRequired[dict],
        "sticker": NotRequired[dict],
        "location": NotRequired[dict],
        "context": NotRequired[dict],
        "interactive": NotRequired[dict],
        "button": NotRequired[dict],
    },
)


def extract_messages(data: dict) -> list[tuple[str, MetaCloudAPIWebhookMessage]]:
    """Extract individual messages from a Meta webhook payload.

    Returns ``(phone_number_id, message)`` tuples — one per logical message after
    grouping. Consecutive text messages from the same user within a single change
    value are merged into a single message: the latest entry by timestamp is kept,
    with earlier text bodies prepended (newline-separated). Non-text messages pass
    through unchanged; mixed-type traffic from the same user yields one merged text
    entry plus the individual non-text entries.

    The grouping key is ``from_user_id`` (falling back to ``from`` for legacy
    payloads that predate BSUID rollout).

    See https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint/
    """
    results: list[tuple[str, MetaCloudAPIWebhookMessage]] = []
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages") or []
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            if not messages or not phone_number_id:
                continue
            for msg in _merge_text_messages_per_user(messages):
                results.append((phone_number_id, msg))
    return results


def _merge_text_messages_per_user(
    messages: list[MetaCloudAPIWebhookMessage],
) -> list[MetaCloudAPIWebhookMessage]:
    """Collapse text messages from the same user into a single message.

    For each user (keyed by ``from_user_id`` or ``from``), all text messages are
    sorted by timestamp and merged — the latest entry is kept as-is except that
    ``text.body`` becomes the newline-joined bodies of every text message in the
    group. The merged entry takes the position of the user's first text message;
    non-text messages pass through in their original position.
    """
    result: list[MetaCloudAPIWebhookMessage] = []
    text_groups: dict[str, list[MetaCloudAPIWebhookMessage]] = {}
    first_idx: dict[str, int] = {}

    for msg in messages:
        if msg.get("type") != "text":
            result.append(msg)
            continue
        user_key = msg.get("from_user_id") or msg.get("from") or ""
        if user_key not in text_groups:
            first_idx[user_key] = len(result)
            text_groups[user_key] = []
            result.append(msg)  # Placeholder; overwritten below if the group has >1 message.
        text_groups[user_key].append(msg)

    for user_key, group in text_groups.items():
        if len(group) == 1:
            continue
        group.sort(key=lambda m: int(m.get("timestamp", "0")))
        merged = cast(MetaCloudAPIWebhookMessage, dict(group[-1]))
        merged["text"] = {**merged.get("text", {}), "body": "\n".join(m["text"]["body"] for m in group)}
        result[first_idx[user_key]] = merged
    return result


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

    expected_signature = signature_header.removeprefix("sha256=")
    computed = hmac.new(
        app_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return constant_time_compare(computed, expected_signature)
