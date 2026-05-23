import base64
import logging
import re
from dataclasses import dataclass
from functools import cached_property
from io import BytesIO
from typing import TYPE_CHECKING, Literal

import phonenumbers
from mailparser_reply import EmailReplyParser
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from apps.channels.models import ChannelPlatform
from apps.chat.channels import MESSAGE_TYPES
from apps.documents.readers import Document
from apps.files.models import File

if TYPE_CHECKING:
    from apps.channels.meta_webhook import MetaCloudAPIWebhookMessage

logger = logging.getLogger("ocs.channels")

AttachmentType = Literal["code_interpreter", "file_search", "ocs_attachments"]


_BSUID_RE = re.compile(r"^[A-Z]{2}(?:\.ENT)?\.[A-Za-z0-9]{1,128}$")


def looks_like_bsuid(value: str) -> bool:
    """Return True if `value` matches the Meta business-scoped user ID format.

    Per Meta's spec, a BSUID is an ISO 3166 alpha-2 country code (two uppercase letters)
    followed by a period and up to 128 alphanumeric characters
    (e.g. US.13491208655302741918). Parent BSUIDs (which work across business portfolios)
    insert an ``ENT.`` between the country code and the identifier
    (e.g. US.ENT.11815799212886844830).

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
            logger.error(f"Attachment with id {self.file_id} not found", exc_info=True, extra=self.model_dump())
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
    phone_number: str | None = Field(default=None)
    """The user's phone number (E.164) when included in the WhatsApp webhook (Twilio `From`
    field). `None` for username-adopters whose phone is not exposed, and for non-WhatsApp
    platforms (Facebook Messenger). Used alongside `participant_id` (BSUID) to match
    legacy phone-keyed participants."""

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
        from_value = message_data["From"].removeprefix(prefix_to_remove)
        phone_number = None

        if platform == ChannelPlatform.WHATSAPP:
            # Parse the number to E164 format, since this is the format of numbers in the DB
            # Normally they are already in this format, but this is just an extra layer of security
            number_obj = phonenumbers.parse(to)
            to = phonenumbers.format_number(number_obj, phonenumbers.PhoneNumberFormat.E164)

            # ExternalUserId (BSUID) is the stable identifier on WhatsApp — Twilio guarantees
            # it on every post-rollout webhook. Strip the `whatsapp:` prefix; missing field
            # means a malformed payload, so the KeyError surfaces.
            participant_id = message_data["ExternalUserId"].removeprefix(prefix_to_remove)
            # `From` carries the phone only when the user has not adopted a username; otherwise
            # it mirrors the BSUID. Normalize to E.164 for matching against legacy phone-keyed
            # participants stored in the DB.
            if not looks_like_bsuid(from_value):
                phone_number = phonenumbers.format_number(
                    phonenumbers.parse(from_value), phonenumbers.PhoneNumberFormat.E164
                )

            # Sending BSUIDs are not yet supported, so we use the phone number for now. Once this is supported,
            # remove this line
            participant_id = phone_number
        else:
            # Facebook Messenger: no BSUID concept; use the sender id as before.
            participant_id = from_value

        return TwilioMessage(
            participant_id=participant_id,
            to=to,
            message_text=message_data["Body"],
            content_type=content_type,
            media_url=message_data.get("MediaUrl0"),
            content_type_unparsed=content_type,
            platform=platform,
            phone_number=phone_number,
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


# Meta/Turn WhatsApp message types that are NOT user-authored conversational
# messages. These payloads may omit the top-level "contacts" array entirely
# (e.g. "system" notifications like user_changed_number).
_NON_CONVERSATIONAL_WA_MESSAGE_TYPES = frozenset({"system", "unsupported"})


def _extract_wa_participant_id(message_data: dict, message: dict) -> str | None:
    """Best-effort extraction of the WhatsApp participant id (wa_id).

    Standard inbound messages include a top-level ``contacts`` array, but
    Meta system/status payloads omit it. In those cases we fall back to the
    ``from`` field on the message itself, or to ``message["system"]["wa_id"]``.
    """
    contacts = message_data.get("contacts") or []
    if contacts:
        wa_id = contacts[0].get("wa_id")
        if wa_id:
            return wa_id
    if message.get("from"):
        return message["from"]
    system = message.get("system") or {}
    return system.get("wa_id")


class TurnWhatsappMessage(BaseMessage):
    to_number: str = Field(default="", required=False)  # This field is needed for the WhatsappChannel
    media_id: str | None = Field(default=None)
    content_type_unparsed: str | None = Field(default=None)

    @field_validator("content_type", mode="before")
    @classmethod
    def determine_content_type(cls, value):
        if value == "audio":
            return MESSAGE_TYPES.VOICE
        if MESSAGE_TYPES.is_member(value):
            return MESSAGE_TYPES(value)

    @staticmethod
    def parse(message_data: dict):
        message = message_data["messages"][0]
        message_type = message["type"]
        # Meta/Turn deliver non-conversational payloads (e.g. type="system" for
        # user_changed_number, or "unsupported") that have a "messages" array
        # but no "contacts" key. These are not user-authored messages, so
        # short-circuit before attempting to dereference contacts.
        if message_type in _NON_CONVERSATIONAL_WA_MESSAGE_TYPES:
            logger.info("Ignoring non-conversational WhatsApp message of type=%s", message_type)
            return None

        body = ""
        if message_type == "text":
            body = message["text"]["body"]

        participant_id = _extract_wa_participant_id(message_data, message)
        if not participant_id:
            logger.info("Ignoring WhatsApp message with no resolvable participant_id (type=%s)", message_type)
            return None

        return TurnWhatsappMessage(
            participant_id=participant_id,
            message_text=body,
            content_type=message_type,
            media_id=message.get(message_type, {}).get("id", None),
            content_type_unparsed=message_type,
        )


class MetaCloudAPIMessage(TurnWhatsappMessage):
    """Message from the Meta Cloud API (WhatsApp Business).

    Extends TurnWhatsappMessage with the WhatsApp message ID, which is required
    for features like typing indicators.
    """

    whatsapp_message_id: str | None = Field(default=None)
    phone_number: str | None = Field(default=None)
    """The user's phone number when included in the webhook (Meta `from` field).
    `None` for username-adopters whose phone is not exposed. Used alongside
    `participant_id` (BSUID) to match legacy phone-keyed participants."""

    @staticmethod
    def parse(message_data: "MetaCloudAPIWebhookMessage | dict") -> "MetaCloudAPIMessage | None":
        message_type = message_data["type"]
        # Meta delivers non-conversational payloads (e.g. type="system" for
        # user_changed_number, or "unsupported") that may omit the identifier
        # fields we expect. Short-circuit before attempting to parse them.
        if message_type in _NON_CONVERSATIONAL_WA_MESSAGE_TYPES:
            logger.info("Ignoring non-conversational Meta Cloud API message of type=%s", message_type)
            return None

        body = ""
        if message_type == "text":
            body = message_data["text"]["body"]

        # BSUID (`from_user_id`) is the stable identifier — it's present on every post-rollout
        # webhook regardless of whether the user adopted a username. A missing field means a
        # malformed payload, so we let the KeyError surface.
        from_value = message_data.get("from")
        # Sending BSUIDs are not yet supported, so we use the phone number (from_value) for now. Once this is supported,
        # remove this line
        participant_id = from_value or message_data["from_user_id"]

        phone_number = from_value if from_value and not looks_like_bsuid(from_value) else None
        return MetaCloudAPIMessage(
            participant_id=participant_id,
            message_text=body,
            content_type=message_type,
            media_id=message_data.get(message_type, {}).get("id", None),
            content_type_unparsed=message_type,
            whatsapp_message_id=message_data.get("id"),
            phone_number=phone_number,
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
