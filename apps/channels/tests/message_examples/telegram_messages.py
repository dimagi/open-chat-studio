import json

from telebot import types

from apps.channels.datamodels import TelegramMessage


def text_message(chat_id: int, message_text: str = "Hi there") -> TelegramMessage:
    message_data = {
        "update_id": 432101234,
        "message": {
            "message_id": 576,
            "from": {
                "id": chat_id,
                "is_bot": False,
                "first_name": "John",
                "last_name": "Doe",
                "username": "doezies",
                "language_code": "en",
            },
            "chat": {
                "id": chat_id,
                "first_name": "John",
                "last_name": "Doe",
                "username": "doezies",
                "type": "private",
            },
            "date": 1690376696,
            "text": message_text,
        },
    }
    json_data = json.dumps(message_data)
    update = types.Update.de_json(json_data)
    return TelegramMessage.parse(update)


def photo_message(chat_id: int) -> TelegramMessage:
    message_data = {
        "update_id": 432101234,
        "message": {
            "message_id": 576,
            "from": {
                "id": chat_id,
                "is_bot": False,
                "first_name": "John",
                "last_name": "Doe",
                "username": "doezies",
                "language_code": "en",
            },
            "chat": {"id": chat_id, "first_name": "John", "last_name": "Doe", "username": "doezies", "type": "private"},
            "date": 1708022365,
            "photo": [
                {
                    "file_id": "AgACAgQAAxkBAAICAAFlzlpco8",
                    "file_unique_id": "AQADVoel4",
                    "file_size": 1443,
                    "width": 90,
                    "height": 67,
                }
            ],
        },
    }
    json_data = json.dumps(message_data)
    update = types.Update.de_json(json_data)
    return TelegramMessage.parse(update)
