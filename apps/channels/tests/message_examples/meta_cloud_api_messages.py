def _wrap_in_webhook_payload(value, phone_number_id="12345"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {
                        "value": value,
                        "field": "messages",
                    }
                ],
            }
        ],
    }


# Post-rollout Meta payloads carry the user's business-scoped user ID (BSUID) as
# `from_user_id` on the message and `user_id` on the contact, alongside the phone (`wa_id`/`from`).
BSUID = "US.13491208655302741918"


def _message_value(*, phone_number_id: str, message_id: str, message_type: str, payload: dict) -> dict:
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512", "user_id": BSUID}],
        "messages": [
            {
                "from": "27456897512",
                "from_user_id": BSUID,
                "id": message_id,
                "timestamp": "1706709716",
                "type": message_type,
                **({message_type: payload} if payload else {}),
            }
        ],
    }


def text_message_value(phone_number_id="12345"):
    return _message_value(
        phone_number_id=phone_number_id,
        message_id="wamid.abc123",
        message_type="text",
        payload={"body": "Hello"},
    )


def text_message(phone_number_id="12345"):
    return _wrap_in_webhook_payload(text_message_value(phone_number_id), phone_number_id)


def audio_message_value(phone_number_id="12345"):
    return _message_value(
        phone_number_id=phone_number_id,
        message_id="wamid.abc456",
        message_type="audio",
        payload={
            "mime_type": "audio/ogg; codecs=opus",
            "sha256": "abc123",
            "id": "1215194677037265",
        },
    )


def audio_message(phone_number_id="12345"):
    return _wrap_in_webhook_payload(audio_message_value(phone_number_id), phone_number_id)


def image_message_value(phone_number_id="12345", caption="Check this out"):
    return _message_value(
        phone_number_id=phone_number_id,
        message_id="wamid.img001",
        message_type="image",
        payload={
            "id": "image-media-id-456",
            "url": "https://cdn.meta.example.com/image-media-id-456",
            "mime_type": "image/jpeg",
            "sha256": "def456",
            "caption": caption,
        },
    )


def image_message_value_no_caption(phone_number_id="12345"):
    return image_message_value(phone_number_id, caption="")


def image_message(phone_number_id="12345", caption="Check this out"):
    return _wrap_in_webhook_payload(image_message_value(phone_number_id, caption=caption), phone_number_id)


def document_message_value(
    phone_number_id="12345",
    caption="Here's the invoice",
    filename="invoice.pdf",
    mime_type="application/pdf",
):
    return _message_value(
        phone_number_id=phone_number_id,
        message_id="wamid.doc001",
        message_type="document",
        payload={
            "id": "document-media-id-789",
            "url": "https://cdn.meta.example.com/document-media-id-789",
            "filename": filename,
            "mime_type": mime_type,
            "sha256": "doc456",
            "caption": caption,
        },
    )


def document_message_value_no_caption(phone_number_id="12345"):
    return document_message_value(phone_number_id, caption="")


def document_message(phone_number_id="12345", caption="Here's the invoice"):
    return _wrap_in_webhook_payload(document_message_value(phone_number_id, caption=caption), phone_number_id)


def system_user_changed_number_value(phone_number_id="12345"):
    """A non-conversational system payload from Meta indicating the user
    changed their WhatsApp number. Note: no top-level ``contacts`` array."""
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "messages": [
            {
                "from": "27456897512",
                "id": "wamid.sys123",
                "timestamp": "1706709716",
                "type": "system",
                "system": {
                    "body": "User changed number from 27000000000 to 27456897512",
                    "new_wa_id": "27456897512",
                    "type": "user_changed_number",
                },
            }
        ],
    }


def system_user_changed_number_message(phone_number_id="12345"):
    return _wrap_in_webhook_payload(system_user_changed_number_value(phone_number_id), phone_number_id)


def unsupported_message_value(phone_number_id="12345"):
    """A non-conversational ``unsupported`` payload (e.g. unknown message type).
    Note: no top-level ``contacts`` array."""
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "messages": [
            {
                "from": "27456897512",
                "id": "wamid.unsup1",
                "timestamp": "1706709716",
                "type": "unsupported",
                "errors": [{"code": 131051, "title": "Message type is not currently supported."}],
            }
        ],
    }


def unsupported_message(phone_number_id="12345"):
    return _wrap_in_webhook_payload(unsupported_message_value(phone_number_id), phone_number_id)
