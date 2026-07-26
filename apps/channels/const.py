from enum import Enum

SLACK_ALL_CHANNELS = "*"


class MESSAGE_TYPES(Enum):
    TEXT = "text"
    VOICE = "voice"
    OTHER = "other"

    @staticmethod
    def is_member(value: str):
        return any(value == item.value for item in MESSAGE_TYPES)
