import json


def text_message(page_id: str, message: str):
    data = {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": 1699259350301,
                "messaging": [
                    {
                        "sender": {"id": "6785984231"},
                        "recipient": {"id": page_id},
                        "timestamp": 1699259349974,
                        "message": {
                            "mid": "m_IAx--vsBAYF3FYqN0LQN3sU3K_suxsIcKASSDH",
                            "text": message,
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(data)


def audio_message(page_id: str, attachment_url: str):
    data = {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": 1699260776574,
                "messaging": [
                    {
                        "sender": {"id": "6785984231"},
                        "recipient": {
                            "id": page_id,
                        },
                        "timestamp": 1699259349974,
                        "message": {
                            "mid": "m_IAx--vsBAYF3FYqN0LQN3sU3K_suxsIcKASSDHASD",
                            "attachments": [{"type": "audio", "payload": {"url": attachment_url}}],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(data)
