def text_message(phone_number_id="12345"):
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
