from django.utils.crypto import constant_time_compare

from apps.api.permissions import get_hmac_digest

SIGNATURE_HEADER = "X-Turn-Hook-Signature"


def verify_signature(payload: bytes, signature_header: str, hmac_secret: str) -> bool:
    """Verify the X-Turn-Hook-Signature header from a Turn.io webhook.

    Turn signs the raw request body with HMAC-SHA256, keyed on the secret configured
    for that webhook in the customer's Turn account, and sends the base64-encoded
    digest. See https://whatsapp.turn.io/docs/api/webhooks

    Returns False for any unusable input rather than raising: a malformed or absent
    header is an authentication failure, not a server error.
    """
    if not signature_header or not hmac_secret:
        return False

    expected = get_hmac_digest(key=hmac_secret.encode(), data_bytes=payload).decode()
    return constant_time_compare(expected, signature_header)
