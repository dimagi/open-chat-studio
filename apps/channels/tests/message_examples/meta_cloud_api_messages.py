def text_message_value(phone_number_id="12345"):
    """Default webhook value: both wa_id/from (phone) and user_id/from_user_id (BSUID)
    present. Represents a non-username-adopter or a username-adopter whose phone is
    visible via the contact book / 30-day cache."""
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512", "user_id": "US.13491208655302741918"}],
        "messages": [
            {
                "from": "27456897512",
                "from_user_id": "US.13491208655302741918",
                "id": "wamid.abc123",
                "timestamp": "1706709716",
                "text": {"body": "Hello"},
                "type": "text",
            }
        ],
    }


def text_message(phone_number_id="12345"):
    """Default full webhook payload: both wa_id/from (phone) and user_id/from_user_id (BSUID)
    present."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {
                        "value": text_message_value(phone_number_id),
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def multi_text_message(phone_number_ids):
    """Build a webhook payload containing one text-message change per phone_number_id."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {
                        "value": text_message_value(phone_number_id),
                        "field": "messages",
                    }
                    for phone_number_id in phone_number_ids
                ],
            }
        ],
    }


def audio_message_value(phone_number_id="12345"):
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512", "user_id": "US.13491208655302741918"}],
        "messages": [
            {
                "from": "27456897512",
                "from_user_id": "US.13491208655302741918",
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
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {
                        "value": audio_message_value(phone_number_id),
                        "field": "messages",
                    }
                ],
            }
        ],
    }


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
                    "wa_id": "27456897512",
                    "type": "user_changed_number",
                },
            }
        ],
    }


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
