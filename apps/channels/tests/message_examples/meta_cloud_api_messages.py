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


def text_message_value(phone_number_id="12345"):
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
        "messages": [
            {
                "from": "27456897512",
                "id": "wamid.abc123",
                "timestamp": "1706709716",
                "text": {"body": "Hello"},
                "type": "text",
            }
        ],
    }


def text_message(phone_number_id="12345"):
    return _wrap_in_webhook_payload(text_message_value(phone_number_id), phone_number_id)


def audio_message_value(phone_number_id="12345"):
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
        "messages": [
            {
                "from": "27456897512",
                "id": "wamid.abc456",
                "timestamp": "1706709716",
                "type": "audio",
                "audio": {
                    "mime_type": "audio/ogg; codecs=opus",
                    "sha256": "abc123",
                    "id": "1215194677037265",
                },
            }
        ],
    }


def audio_message(phone_number_id="12345"):
    return _wrap_in_webhook_payload(audio_message_value(phone_number_id), phone_number_id)


def image_message_value(phone_number_id="12345", caption="Check this out"):
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
        "messages": [
            {
                "from": "27456897512",
                "id": "wamid.img001",
                "timestamp": "1706709716",
                "type": "image",
                "image": {
                    "id": "image-media-id-456",
                    "url": "https://cdn.meta.example.com/image-media-id-456",
                    "mime_type": "image/jpeg",
                    "sha256": "def456",
                    "caption": caption,
                },
            }
        ],
    }


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
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
        "messages": [
            {
                "from": "27456897512",
                "id": "wamid.doc001",
                "timestamp": "1706709716",
                "type": "document",
                "document": {
                    "id": "document-media-id-789",
                    "url": "https://cdn.meta.example.com/document-media-id-789",
                    "filename": filename,
                    "mime_type": mime_type,
                    "sha256": "doc456",
                    "caption": caption,
                },
            }
        ],
    }


def document_message_value_no_caption(phone_number_id="12345"):
    return document_message_value(phone_number_id, caption="")


def document_message(phone_number_id="12345", caption="Here's the invoice"):
    return _wrap_in_webhook_payload(document_message_value(phone_number_id, caption=caption), phone_number_id)
