import base64
import logging
from functools import cached_property
from io import BytesIO
from typing import Literal

import phonenumbers
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.channels.models import ChannelPlatform
from apps.chat.channels import MESSAGE_TYPES

logger = logging.getLogger("ocs.channels")

AttachmentType = Literal["code_interpreter", "file_search", "ocs_attachments"]


class MediaCache(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    content_type: str
    data: BytesIO


class Attachment(BaseModel):
    file_id: int
    type: AttachmentType
    name: str
    size: int = Field(..., ge=0)
    content_type: str = "application/octet-stream"
    download_link: str

    upload_to_assistant: bool = False
    """Setting this to True will cause the Assistant Node to send the attachment
    as a file attachment with the message."""

    @classmethod
    def from_file(cls, file, type: AttachmentType, session_id: int):
        return cls(
            file_id=file.id,
            type=type,
            name=file.name,
            size=file.content_size,
            content_type=file.content_type,
            download_link=file.download_link(session_id),
        )

    @property
    def id(self):
        return self.file_id

    @cached_property
    def _file(self):
        from apps.files.models import File

        try:
            return File.objects.get(id=self.file_id)
        except File.DoesNotExist:
            logger.error(f"Attachment with id {self.file_id} not found", exc_info=True, extra=self.model_dump())
            return None

    @cached_property
    def document(self):
        from apps.documents.readers import Document

        return Document.from_file(self._file)

    def read_bytes(self):
        if not self._file:
            return b""
        return self._file.file.read()

    def read_text(self):
        return self.document.get_contents_as_string()

    def read_base64(self):
        data = self.read_bytes()
        return base64.b64encode(data).decode("utf-8")


class BaseMessage(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    participant_id: str
    message_text: str
    content_type: MESSAGE_TYPES | None = Field(default=MESSAGE_TYPES.TEXT)
    attachments: list[Attachment] = Field(default=[])

    cached_media_data: MediaCache | None = Field(default=None)


class TelegramMessage(BaseMessage):
    media_id: str | None = None
    content_type_unparsed: str | None = Field(default=None)
    message_id: int

    @field_validator("content_type", mode="before")
    @classmethod
    def determine_content_type(cls, value):
        if MESSAGE_TYPES.is_member(value):
            return MESSAGE_TYPES(value)

    @staticmethod
    def parse(update_obj) -> "TelegramMessage":
        return TelegramMessage(
            participant_id=str(update_obj.message.chat.id),
            message_text=update_obj.message.text or "",
            content_type=update_obj.message.content_type,
            media_id=update_obj.message.voice.file_id if update_obj.message.content_type == "voice" else None,
            message_id=update_obj.message.message_id,
            content_type_unparsed=update_obj.message.content_type,
        )


class TwilioMessage(BaseMessage):
    """
    A wrapper class for user messages coming from the twilio
    """

    to: str
    platform: ChannelPlatform
    media_url: str | None = Field(default=None)
    content_type_unparsed: str | None = Field(default=None)

    @field_validator("content_type", mode="before")
    @classmethod
    def determine_content_type(cls, value):
        if not value:
            # Normal test messages doesn't have a content type
            return MESSAGE_TYPES.TEXT
        if value and value in ["audio/ogg", "video/mp4"]:
            return MESSAGE_TYPES.VOICE
        return MESSAGE_TYPES.OTHER

    @staticmethod
    def parse(message_data: dict) -> "TwilioMessage":
        prefix_channel_map = {"messenger": ChannelPlatform.FACEBOOK, "whatsapp": ChannelPlatform.WHATSAPP}
        prefix = message_data["From"].split(":")[0]
        content_type = message_data.get("MediaContentType0")

        prefix_to_remove = f"{prefix}:"
        platform = prefix_channel_map[prefix]
        to = message_data["To"].removeprefix(prefix_to_remove)
        if platform == ChannelPlatform.WHATSAPP:
            # Parse the number to E164 format, since this is the format of numbers in the DB
            # Normally they are already in this format, but this is just an extra layer of security
            number_obj = phonenumbers.parse(to)
            to = phonenumbers.format_number(number_obj, phonenumbers.PhoneNumberFormat.E164)
        return TwilioMessage(
            participant_id=message_data["From"].removeprefix(prefix_to_remove),
            to=to,
            message_text=message_data["Body"],
            content_type=content_type,
            media_url=message_data.get("MediaUrl0"),
            content_type_unparsed=content_type,
            platform=platform,
        )


class SureAdhereMessage(BaseMessage):
    """
    A wrapper class for user messages coming from the SureAdhere
    """

    @staticmethod
    def parse(message_data: dict) -> "SureAdhereMessage":
        return SureAdhereMessage(
            participant_id=str(message_data["patient_id"]), message_text=message_data["message_text"]
        )


class TurnWhatsappMessage(BaseMessage):
    to_number: str = Field(default="", required=False)  # This field is needed for the WhatsappChannel
    media_id: str | None = Field(default=None)
    content_type_unparsed: str | None = Field(default=None)

    @field_validator("content_type", mode="before")
    @classmethod
    def determine_content_type(cls, value):
        if MESSAGE_TYPES.is_member(value):
            return MESSAGE_TYPES(value)

    @staticmethod
    def parse(message_data: dict):
        message = message_data["messages"][0]
        message_type = message["type"]
        body = ""
        if message_type == "text":
            body = message["text"]["body"]

        return TurnWhatsappMessage(
            participant_id=message_data["contacts"][0]["wa_id"],
            message_text=body,
            content_type=message_type,
            media_id=message.get(message_type, {}).get("id", None),
            content_type_unparsed=message_type,
        )


class FacebookMessage(BaseMessage):
    """
    A wrapper class for user messages coming from Facebook
    """

    page_id: str
    media_url: str | None = None
    content_type_unparsed: str | None = Field(default=None)

    @field_validator("content_type", mode="before")
    @classmethod
    def determine_content_type(cls, value):
        if not value:
            return MESSAGE_TYPES.TEXT
        if value and value == "audio":
            return MESSAGE_TYPES.VOICE

    @staticmethod
    def parse(message_data: dict) -> "FacebookMessage":
        page_id = message_data["recipient"]["id"]
        attachments = message_data["message"].get("attachments", [])
        content_type = None
        media_url = None

        if len(attachments) > 0:
            attachment = attachments[0]
            media_url = attachment["payload"]["url"]
            content_type = attachment["type"]

        return FacebookMessage(
            participant_id=message_data["sender"]["id"],
            page_id=page_id,
            message_text=message_data["message"].get("text", ""),
            media_url=media_url,
            content_type=content_type,
            content_type_unparsed=content_type,
        )


class SlackMessage(BaseMessage):
    channel_id: str
    thread_ts: str
    message_text: str
