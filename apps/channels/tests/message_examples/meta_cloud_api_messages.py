# Note: the webhook-envelope wrapper is intentionally duplicated across the full-payload
# builders below rather than extracted to a helper. The explicit shape makes it easy to
# see what an inbound Meta payload actually looks like when reading the fixtures alongside
# the tests.


def legacy_text_message_value(phone_number_id="12345"):
    """Pre-BSUID webhook value: only wa_id / from are present, no user_id fields."""
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


def legacy_text_message(phone_number_id="12345"):
    """Pre-BSUID full webhook payload: only wa_id / from are present, no user_id fields."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {
                        "value": {
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
                        },
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
                        "value": {
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
                        },
                        "field": "messages",
                    }
                    for phone_number_id in phone_number_ids
                ],
            }
        ],
    }


def text_message_with_user_id_and_wa_id_value(phone_number_id="12345"):
    """Dual-field webhook value: both wa_id and user_id present. Represents the
    early-rollout / non-username-adopter / contact-book-populated case."""
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "contacts": [
            {
                "profile": {"name": "User"},
                "wa_id": "27456897512",
                "user_id": "US.13491208655302741918",
            }
        ],
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


def text_message_with_user_id_and_wa_id(phone_number_id="12345"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {
                        "value": text_message_with_user_id_and_wa_id_value(phone_number_id),
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def text_message_with_username_and_wa_id_value(phone_number_id="12345"):
    """Username-adopter whose phone is still visible via contact book / 30-day cache."""
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "contacts": [
            {
                "profile": {"name": "User", "username": "@testusername"},
                "wa_id": "27456897512",
                "user_id": "US.13491208655302741918",
            }
        ],
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


def text_message_with_username_and_wa_id(phone_number_id="12345"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {
                        "value": text_message_with_username_and_wa_id_value(phone_number_id),
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def text_message_user_id_only_value(phone_number_id="12345"):
    """Username-adopter whose phone is unavailable: only user_id / from_user_id present."""
    return {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": "+15551234567",
            "phone_number_id": phone_number_id,
        },
        "contacts": [
            {
                "profile": {"name": "User", "username": "@testusername"},
                "user_id": "US.13491208655302741918",
            }
        ],
        "messages": [
            {
                "from_user_id": "US.13491208655302741918",
                "id": "wamid.abc123",
                "timestamp": "1706709716",
                "text": {"body": "Hello"},
                "type": "text",
            }
        ],
    }


def text_message_user_id_only(phone_number_id="12345"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {
                        "value": text_message_user_id_only_value(phone_number_id),
                        "field": "messages",
                    }
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
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BIZ_ID",
                "changes": [
                    {
                        "value": {
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
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
