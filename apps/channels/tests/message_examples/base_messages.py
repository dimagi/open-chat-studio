from apps.channels.datamodels import BaseMessage
from apps.chat.channels import MESSAGE_TYPES


def text_message(participant_id: str = "123", message_text: str = "Hi") -> BaseMessage:
    return BaseMessage(participant_id=participant_id, message_text=message_text)


def audio_message(participant_id: str = "123") -> BaseMessage:
    return BaseMessage(participant_id=participant_id, message_text="", content_type=MESSAGE_TYPES.VOICE)


def unsupported_content_type_message(
    participant_id: str = "123",
) -> BaseMessage:
    return BaseMessage(participant_id=participant_id, message_text="", content_type=None)
