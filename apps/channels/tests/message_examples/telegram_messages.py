import json

from telebot import types

from apps.channels.datamodels import TelegramMessage


def text_message(chat_id: int, message_text: str = "Hi there") -> types.Message:
    message_data = {
        "update_id": 432101234,
        "message": {
            "message_id": 576,
            "from": {
                "id": chat_id,
                "is_bot": False,
                "first_name": "Chris",
                "last_name": "Smit",
                "username": "smittiec",
                "language_code": "en",
            },
            "chat": {
                "id": chat_id,
                "first_name": "Chris",
                "last_name": "Smit",
                "username": "smittiec",
                "type": "private",
            },
            "date": 1690376696,
            "text": message_text,
        },
    }
    json_data = json.dumps(message_data)
    update = types.Update.de_json(json_data)
    return TelegramMessage.parse(update)
