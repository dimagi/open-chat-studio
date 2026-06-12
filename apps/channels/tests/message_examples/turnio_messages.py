def _vnd(
    *,
    author_name="User",
    chat_uuid,
    contact_uuid,
    permalink_uuid,
    message_uuid,
    inserted_at,
    chat_updated_at,
    unread_count,
    chat_inserted_at="2024-01-25T09:02:46.684610Z",
):
    return {
        "v1": {
            "author": {"id": "27456897512", "name": author_name, "type": "OWNER"},
            "card_uuid": "None",
            "chat": {
                "assigned_to": "None",
                "contact_uuid": contact_uuid,
                "inserted_at": chat_inserted_at,
                "owner": "+27456897512",
                "permalink": f"https://whatsapp.turn.io/app/c/{permalink_uuid}",
                "state": "OPEN",
                "state_reason": "Re-opened by inbound message.",
                "unread_count": unread_count,
                "updated_at": chat_updated_at,
                "uuid": chat_uuid,
            },
            "direction": "inbound",
            "faq_uuid": "None",
            "in_reply_to": "None",
            "inserted_at": inserted_at,
            "labels": [],
            "last_status": "None",
            "last_status_timestamp": "None",
            "on_fallback_channel": False,
            "rendered_content": "None",
            "uuid": message_uuid,
        }
    }


def text_message():
    return {
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
        "messages": [
            {
                "_vnd": _vnd(
                    chat_uuid="08a64841-32123-4111-b91f-4ff36d676c1c",
                    contact_uuid="08a64841-32123-4111-b91f-4ff36d676c1c",
                    permalink_uuid="08a64841-32123-4111-b91f-4ff36d676c1c",
                    message_uuid="08a64841-32123-4111-b91f-4ff36d676c1c",
                    inserted_at="2024-01-31T14:01:56.467839Z",
                    chat_updated_at="2024-01-31T14:01:56.486313Z",
                    unread_count=1,
                ),
                "from": "27456897512",
                "id": "ABCDEFGHIJKL_Ags-sF0gx5ts0DDMxw",
                "text": {"body": "Hi there!"},
                "timestamp": "1706709716",
                "type": "text",
            }
        ],
    }


def outbound_message():
    """An outbound message is one that the bot sends to the user. Depending on how the user configures the webhook,
    Turn.io might forward any outbound messages to the server as well.
    """

    return {
        "_vnd": {
            "v1": {
                "author": {"id": "ef59c32dsd9-289d-474e-aab1-e0dsss19f1f4", "name": "Dev token", "type": "SYSTEM"},
                "card_uuid": None,
                "chat": {
                    "assigned_to": None,
                    "contact_uuid": "eeb51508-4ff0-4ca4-9bf8-69e548b1ceb3",
                    "inserted_at": "2024-01-25T09:02:46.684610Z",
                    "owner": "+0723456789",
                    "permalink": "https://whatsapp.turn.io/app/c/08a64841-10df-4c11-b81f-4ec36d616c1c",
                    "state": "OPEN",
                    "state_reason": "Re-opened by inbound message.",
                    "unread_count": 54,
                    "updated_at": "2024-03-12T09:16:38.690961Z",
                    "uuid": "ef59c32dsd9-289d-474e-aab1-e0dsss19f1f4",
                },
                "direction": "outbound",
                "faq_uuid": None,
                "in_reply_to": None,
                "inserted_at": "2024-03-12T09:16:38.678761Z",
                "labels": [],
                "last_status": None,
                "last_status_timestamp": None,
                "on_fallback_channel": False,
                "rendered_content": None,
                "uuid": "761bcbf9-53b1-043f-be33-e74c7d3fc86d",
            }
        },
        "from": "15550104171",
        "id": "wamid.HBgLMjc4MjY0MTk5NzcVAgARGBI2NThCNjVCOEUxMzZBMjhDMDMA",
        "preview_url": False,
        "recipient_type": "individual",
        "text": {"body": "Guten Tag!"},
        "timestamp": "1710234998",
        "to": "0723456789",
        "type": "text",
    }


def status_message():
    return {
        "statuses": [
            {
                "id": "ABGGFlA5FpafAgo6tHcNmNjXmuSf",
                "status": "sent",
                "timestamp": "1518694700",
                "message": {"recipient_id": "16315555555"},
            }
        ]
    }


def audio_message():
    return {
        "contacts": [{"wa_id": "27826419977", "profile": {"name": "Test"}}],
        "messages": [
            {
                "from": "27826419977",
                "id": "wamid.test",
                "timestamp": "1773300527",
                "type": "audio",
                "audio": {
                    "mime_type": "audio/ogg; codecs=opus",
                    "sha256": "abc123",
                    "id": "1215194677037265",
                },
            }
        ],
    }


def image_message(caption="Look at this!"):
    return {
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
        "messages": [
            {
                "from": "27456897512",
                "id": "ABCDimgXYZ",
                "timestamp": "1706709716",
                "type": "image",
                "image": {
                    "id": "turn-image-media-id-789",
                    "url": "https://media.turn.io/turn-image-media-id-789",
                    "mime_type": "image/jpeg",
                    "sha256": "ghi789",
                    "caption": caption,
                },
            }
        ],
    }


def document_message(caption="Here's the report", filename="report.pdf", mime_type="application/pdf"):
    return {
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
        "messages": [
            {
                "from": "27456897512",
                "id": "ABCDdocXYZ",
                "timestamp": "1706709716",
                "type": "document",
                "document": {
                    "id": "turn-document-media-id-789",
                    "url": "https://media.turn.io/turn-document-media-id-789",
                    "mime_type": mime_type,
                    "filename": filename,
                    "sha256": "docghi789",
                    "caption": caption,
                },
            }
        ],
    }


def system_user_changed_number_message():
    """A non-conversational ``system`` payload (e.g. user_changed_number)
    that Turn.io forwards from Meta. Note: no top-level ``contacts`` array."""
    return {
        "messages": [
            {
                "from": "27456897512",
                "id": "wamid.sys456",
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


def unsupported_message():
    """A non-conversational ``unsupported`` payload (e.g. unknown message type)
    that Turn.io forwards from Meta. Note: no top-level ``contacts`` array."""
    return {
        "messages": [
            {
                "from": "27456897512",
                "id": "wamid.unsup2",
                "timestamp": "1706709716",
                "type": "unsupported",
                "errors": [{"code": 131051, "title": "Message type is not currently supported."}],
            }
        ],
    }


def voice_message():
    return {
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
        "messages": [
            {
                "_vnd": _vnd(
                    author_name="John Doe",
                    chat_uuid="08a64841-10df-4c11-b81f-4ec36d616c1c",
                    contact_uuid="ef59c32dsd9-289d-474e-aab1-e0dsss19f1f4",
                    permalink_uuid="ef59c32dsd9-289d-474e-aab1-e0dsss19f1f4",
                    message_uuid="bd788d51-f3e1-11ff-e31d-0fc372a61d66",
                    inserted_at="2024-02-08T12:07:45.891699Z",
                    chat_updated_at="2024-02-08T12:07:46.091528Z",
                    unread_count=31,
                ),
                "from": "27456897512",
                "id": "ABGKLKLKLZd_Ags-DSDSdsWQUpsLqg",
                "timestamp": "1707394065",
                "type": "voice",
                "voice": {
                    "id": "180e1c3f-ae50-481b-a9f0-7c698233965f",
                    "mime_type": "audio/ogg; codecs=opus",
                    "sha256": "407d8ac9d98ddddddd78c7bae4179ea131b55740214ccc42373c85d63aeb55b7",
                    "status": "downloaded",
                },
            }
        ],
    }
