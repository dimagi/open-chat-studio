def text_message():
    return {
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
        "messages": [
            {
                "_vnd": {
                    "v1": {
                        "author": {"id": "27456897512", "name": "User", "type": "OWNER"},
                        "card_uuid": "None",
                        "chat": {
                            "assigned_to": "None",
                            "contact_uuid": "08a64841-32123-4111-b91f-4ff36d676c1c",
                            "inserted_at": "2024-01-25T09:02:46.684610Z",
                            "owner": "+27456897512",
                            "permalink": "https://whatsapp.turn.io/app/c/08a64841-32123-4111-b91f-4ff36d676c1c",
                            "state": "OPEN",
                            "state_reason": "Re-opened by inbound message.",
                            "unread_count": 1,
                            "updated_at": "2024-01-31T14:01:56.486313Z",
                            "uuid": "08a64841-32123-4111-b91f-4ff36d676c1c",
                        },
                        "direction": "inbound",
                        "faq_uuid": "None",
                        "in_reply_to": "None",
                        "inserted_at": "2024-01-31T14:01:56.467839Z",
                        "labels": [],
                        "last_status": "None",
                        "last_status_timestamp": "None",
                        "on_fallback_channel": False,
                        "rendered_content": "None",
                        "uuid": "08a64841-32123-4111-b91f-4ff36d676c1c",
                    }
                },
                "from": "27456897512",
                "id": "ABCDEFGHIJKL_Ags-sF0gx5ts0DDMxw",
                "text": {"body": "Hi there!"},
                "timestamp": "1706709716",
                "type": "text",
            }
        ],
    }


def audio_message():
    return {
        "contacts": [{"profile": {"name": "User"}, "wa_id": "27456897512"}],
        "messages": [
            {
                "_vnd": {
                    "v1": {
                        "author": {"id": "27456897512", "name": "Chris Smit", "type": "OWNER"},
                        "card_uuid": "None",
                        "chat": {
                            "assigned_to": "None",
                            "contact_uuid": "eeb51508-4ff0-4ca4-9bf8-69e548b1ceb3",
                            "inserted_at": "2024-01-25T09:02:46.684610Z",
                            "owner": "+27456897512",
                            "permalink": "https://whatsapp.turn.io/app/c/08a64841-10df-4c11-b81f-4ec36d616c1c",
                            "state": "OPEN",
                            "state_reason": "Re-opened by inbound message.",
                            "unread_count": 31,
                            "updated_at": "2024-02-08T12:07:46.091528Z",
                            "uuid": "08a64841-10df-4c11-b81f-4ec36d616c1c",
                        },
                        "direction": "inbound",
                        "faq_uuid": "None",
                        "in_reply_to": "None",
                        "inserted_at": "2024-02-08T12:07:45.891699Z",
                        "labels": [],
                        "last_status": "None",
                        "last_status_timestamp": "None",
                        "on_fallback_channel": False,
                        "rendered_content": "None",
                        "uuid": "bd788d51-f3e1-11ff-e31d-0fc372a61d66",
                    }
                },
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
