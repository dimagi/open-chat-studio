import base64
import logging
import re
from dataclasses import dataclass
from functools import cached_property
from io import BytesIO
from typing import Literal

import phonenumbers
from mailparser_reply import EmailReplyParser
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from apps.channels.models import ChannelPlatform
from apps.chat.channels import MESSAGE_TYPES
from apps.documents.readers import Document
from apps.files.models import File

logger = logging.getLogger("ocs.channels")

AttachmentType = Literal["code_interpreter", "file_search", "ocs_attachments"]


_BSUID_RE = re.compile(r"^[A-Z]{2}(?:\.ENT)?\.[A-Za-z0-9]{1,128}$")


def looks_like_bsuid(value: str) -> bool:
    """Return True if `value` matches Meta's business-scoped user ID (BSUID) format.

    A BSUID is an ISO 3166 alpha-2 country code (two uppercase letters) followed by a period
    and up to 128 alphanumeric characters (e.g. US.13491208655302741918). Parent BSUIDs
    insert ``ENT.`` between the country code and the identifier (e.g. US.ENT.118157992128868).

    See https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids
    """
    return bool(_BSUID_RE.match(value))


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

    send_to_llm: bool = True
    """Setting this to False will prevent the attachment from being sent to the LLM node."""

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
        try:
            return File.objects.get(id=self.file_id)
        except File.DoesNotExist:
            logger.error(
                f"Attachment with id {self.file_id} not found",
                exc_info=True,
                extra={"attachment": self.model_dump(mode="json")},
            )
            return None

    @cached_property
    def document(self):
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
    remote_id: str | None = Field(default=None)
    message_text: str
    content_type: MESSAGE_TYPES | None = Field(default=MESSAGE_TYPES.TEXT)
    attachments: list[Attachment] = Field(default=[])
    attachment_file_ids: list[int] = Field(default=[])
    """File IDs for attachments persisted by the channel's inbound handler.
    Hydrated into `attachments` by AttachmentHydrationStage once a session
    exists. Channels that don't pre-persist files leave this empty."""

    cached_media_data: MediaCache | None = Field(default=None, exclude=True)


class TelegramMessage(BaseMessage):
    media_id: str | None = None
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
        )


class TwilioMessage(BaseMessage):
    """
    A wrapper class for user messages coming from the twilio
    """

    # Twilio delivers one inbound webhook per attachment — an attachment is really a message
    # whose caption (Body) is the text, so each attached file arrives as its own TwilioMessage.
    to: str
    platform: ChannelPlatform
    media_url: str | None = Field(default=None)
    attachment_mime_type: str | None = Field(default=None)
    attachment_filename: str | None = Field(default=None)

    @field_validator("content_type", mode="before")
    @classmethod
    def determine_content_type(cls, value):
        if not value:
            return MESSAGE_TYPES.TEXT
        match value:
            case "audio" | "voice":
                return MESSAGE_TYPES.VOICE
            case "text" | "image" | "document":
                # image/document inbound messages flow as TEXT so they pass MessageTypeValidationStage;
                # the caption (Body) is the message text and the raw MIME lives in attachment_mime_type.
                return MESSAGE_TYPES.TEXT
            case _:
                return MESSAGE_TYPES.OTHER

    @staticmethod
    def get_bsuid(message_data: dict) -> str | None:
        """Return the BSUID from Twilio's `ExternalUserId` field, or None.

        Twilio sends it on every WhatsApp webhook from June 2026 onwards and it is the stable
        participant identifier. Pre-rollout messages don't carry it, so we return None and the
        caller falls back to the phone number.
        """
        _, _, bsuid = message_data.get("ExternalUserId", "").partition(":")
        return bsuid or None

    @staticmethod
    def get_phone_number(message_data: dict) -> str | None:
        """Return the user's E.164 phone number from `From` for WhatsApp messages, else None."""
        prefix, _, raw = message_data["From"].partition(":")
        if prefix != "whatsapp" or looks_like_bsuid(raw):
            return None
        # Normalize to E.164 (the DB format); numbers usually arrive in this format already.
        return phonenumbers.format_number(phonenumbers.parse(raw), phonenumbers.PhoneNumberFormat.E164)

    @staticmethod
    def parse(message_data: dict) -> "TwilioMessage":
        prefix_channel_map = {"messenger": ChannelPlatform.FACEBOOK, "whatsapp": ChannelPlatform.WHATSAPP}
        prefix = message_data["From"].split(":")[0]
        message_type = message_data.get("MessageType", "text")

        prefix_to_remove = f"{prefix}:"
        platform = prefix_channel_map[prefix]
        to = message_data["To"].removeprefix(prefix_to_remove)
        from_value = message_data["From"].removeprefix(prefix_to_remove)
        phone_number = None
        participant_id = from_value
        if platform == ChannelPlatform.WHATSAPP:
            # Parse the number to E164 format, since this is the format of numbers in the DB
            # Normally they are already in this format, but this is just an extra layer of security
            number_obj = phonenumbers.parse(to)
            to = phonenumbers.format_number(number_obj, phonenumbers.PhoneNumberFormat.E164)

            phone_number = TwilioMessage.get_phone_number(message_data)
            # The BSUID (ExternalUserId) is the stable identifier; fall back to the phone for
            # pre-rollout messages that don't carry one.
            participant_id = TwilioMessage.get_bsuid(message_data) or phone_number
        return TwilioMessage(
            participant_id=participant_id,
            to=to,
            message_text=message_data["Body"],
            content_type=message_type,
            media_url=message_data.get("MediaUrl0"),
            attachment_mime_type=message_data.get("MediaContentType0"),
            platform=platform,
            remote_id=phone_number,
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


_NON_CONVERSATIONAL_WA_MESSAGE_TYPES = frozenset({"system", "unsupported", "unknown"})


def is_non_conversational_whatsapp_message(message_data: dict) -> bool:
    """True for Meta/Turn WhatsApp payloads that are not user-authored conversational
    messages (e.g. type="system" user_changed_number, or "unsupported"/"unknown").

    "system" payloads have a "messages" array but no "contacts" key, so the webhook
    views use this to skip them before dispatching a task that would KeyError while
    parsing. "unsupported"/"unknown" payloads are skipped as there is nothing to process.
    """
    messages = message_data.get("messages") or []
    if not messages:
        return False
    return messages[0].get("type") in _NON_CONVERSATIONAL_WA_MESSAGE_TYPES


class WhatsAppMessage(BaseMessage):
    """Base class for WhatsApp messages (Turn.io and Meta Cloud API)."""

    to_number: str = Field(default="")  # This field is needed for the WhatsappChannel
    media_id: str | None = Field(default=None)
    media_url: str | None = Field(default=None)
    attachment_mime_type: str | None = Field(default=None)
    attachment_filename: str | None = Field(default=None)
    whatsapp_message_id: str | None = Field(default=None)

    @field_validator("content_type", mode="before")
    @classmethod
    def determine_content_type(cls, value):
        match value:
            case "audio":
                return MESSAGE_TYPES.VOICE
            case "image" | "document":
                # Treat as a text message with an attachment; the caption becomes the message text.
                # The actual MIME type is preserved in attachment_mime_type for the persistence helper.
                return MESSAGE_TYPES.TEXT
            case _ if MESSAGE_TYPES.is_member(value):
                return MESSAGE_TYPES(value)
            case _:
                # Unknown/unsupported provider types (e.g. sticker, location) fall back to
                # OTHER so content_type is never None and downstream support checks behave.
                return MESSAGE_TYPES.OTHER

    @staticmethod
    def get_bsuid(message_data: dict) -> str | None:
        """Return the Meta business-scoped user ID (BSUID), or None.

        Meta Cloud API sends it as `from_user_id` on every post-rollout webhook and it is the
        stable participant identifier. Turn.io has no BSUID concept and pre-rollout messages
        don't carry it, so we return None and the caller falls back to the phone number.
        """
        return message_data["messages"][0].get("from_user_id")

    @staticmethod
    def get_phone_number(message_data: dict) -> str | None:
        """Return the user's phone number (Meta `wa_id`)"""
        contacts = message_data.get("contacts") or [{}]
        return contacts[0].get("wa_id")

    @classmethod
    def parse(cls, message_data: dict) -> "WhatsAppMessage":
        message = message_data["messages"][0]
        message_type = message["type"]
        body = ""
        if message_type == "text":
            body = message["text"]["body"]
        elif message_type in ("image", "document"):
            body = message.get(message_type, {}).get("caption", "")

        media_payload = message.get(message_type, {})
        # For documents the provider gives us a real MIME type; for images we keep the
        # literal "image" marker (Twilio still uses image/* — the hydration stage handles both).
        attachment_mime_type: str | None = message_type
        if message_type == "document":
            attachment_mime_type = media_payload.get("mime_type") or "application/octet-stream"

        # The BSUID is the stable identifier; fall back to the phone for pre-rollout / Turn.io
        # messages that don't carry one.
        phone_number = cls.get_phone_number(message_data)
        participant_id = cls.get_bsuid(message_data) or phone_number
        return cls(
            participant_id=participant_id,
            message_text=body,
            content_type=message_type,
            media_id=media_payload.get("id"),
            media_url=media_payload.get("url"),
            attachment_mime_type=attachment_mime_type,
            attachment_filename=media_payload.get("filename") if message_type == "document" else None,
            whatsapp_message_id=message.get("id"),
            remote_id=phone_number,
        )


class FacebookMessage(BaseMessage):
    """
    A wrapper class for user messages coming from Facebook
    """

    page_id: str
    media_url: str | None = None

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
        )


class SlackMessage(BaseMessage):
    channel_id: str
    thread_ts: str
    message_text: str


@dataclass
class RawAttachment:
    """In-memory carrier for an inbound email attachment between parse() and
    the persistence helper. Never serialized; lives only in handler scope."""

    filename: str
    content_type: str
    content_bytes: bytes


class SkippedAttachment(BaseModel):
    """Inbound attachment that was rejected at intake. Reported to the LLM
    via injected notes in the user's message_text."""

    name: str
    reason: str
    size: int = 0


class EmailMessage(BaseMessage):
    """Inbound email parsed from AnymailInboundMessage."""

    from_address: str = Field(max_length=254)
    to_address: str = Field(max_length=254)
    subject: str = Field(max_length=1000)
    message_id: str = Field(max_length=500)
    in_reply_to: str | None = None
    references: list[str] = Field(default=[])
    skipped_attachments: list[SkippedAttachment] = Field(default=[])

    _raw_attachments: list[RawAttachment] = PrivateAttr(default_factory=list)

    @staticmethod
    def parse(inbound) -> "EmailMessage":
        body = inbound.text or ""
        reply = EmailReplyParser(languages=["en", "de", "fr", "es", "pt", "it", "nl", "pl", "sv", "da", "no"]).read(
            body
        )
        stripped_text = reply.latest_reply or body

        message = EmailMessage(
            participant_id=inbound.from_email.addr_spec,
            message_text=stripped_text,
            from_address=inbound.from_email.addr_spec,
            to_address=inbound.to[0].addr_spec if inbound.to else "",
            subject=inbound.subject or "",
            message_id=inbound.get("Message-ID", ""),
            in_reply_to=inbound.get("In-Reply-To"),
            references=_parse_references(inbound.get("References", "")),
        )
        message._raw_attachments = _extract_raw_attachments(inbound)
        return message


def _extract_raw_attachments(inbound) -> list[RawAttachment]:
    """Pull MIMEPart objects from the inbound message into in-memory RawAttachment records.
    AnymailInboundMessage.attachments already excludes inlines."""
    raw = []
    for part in getattr(inbound, "attachments", None) or []:
        try:
            content_type = (part.get_content_type() or "application/octet-stream").split(";")[0].strip().lower()
            raw.append(
                RawAttachment(
                    filename=part.get_filename() or "attachment",
                    content_type=content_type,
                    content_bytes=part.get_content_bytes(),
                )
            )
        except Exception:
            logger.exception("Failed to read inbound email attachment part")
    return raw


_MAX_REFERENCES = 50


def _parse_references(refs: str) -> list[str]:
    """Parse space-separated Message-ID list from References header.

    Keeps the first reference (root message / session anchor) plus the
    most recent ones to prevent unbounded growth in long email threads.
    """
    if not refs:
        return []
    parsed = [r.strip() for r in refs.split() if r.strip()]
    if len(parsed) <= _MAX_REFERENCES:
        return parsed
    return [parsed[0], *parsed[-(_MAX_REFERENCES - 1) :]]
