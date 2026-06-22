import logging
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from typing import IO, TYPE_CHECKING, ClassVar, cast
from urllib.parse import urljoin

import backoff
import httpx
import phonenumbers
import pydantic
import requests
from django.conf import settings
from django.utils import timezone
from telebot.util import smart_split

if TYPE_CHECKING:
    from slack_sdk import WebClient
    from turn import TurnClient
    from twilio.rest import Client
    from twilio.rest.api.v2010.account.message import MessageInstance

from apps.channels import audio
from apps.channels.datamodels import MediaCache, TwilioMessage, WhatsAppMessage
from apps.channels.models import ChannelPlatform
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.exceptions import ServiceWindowExpiredException
from apps.files.models import File
from apps.service_providers.exceptions import MessageMediaError, ServiceProviderConfigError
from apps.service_providers.file_limits import can_send_on_whatsapp
from apps.service_providers.s3 import get_s3_client
from apps.service_providers.speech_service import SynthesizedAudio

logger = logging.getLogger("ocs.messaging")

MEDIA_DOWNLOAD_TIMEOUT = 30


def _normalize_content_type(content_type: str | None) -> str:
    return (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()


def _is_voice_mime(mime: str | None) -> bool:
    """True when the attachment is a voice/audio message — those are routed via
    get_message_audio, not the generic inbound-media path."""
    if not mime:
        return False
    normalized = mime.split(";", 1)[0].strip().lower()
    return normalized in ("audio", "voice") or normalized.startswith("audio/") or normalized == "video/mp4"


def _has_downloadable_attachment(message) -> bool:
    """True when the message references a non-voice attachment we can fetch."""
    mime = message.attachment_mime_type
    if not mime or _is_voice_mime(mime):
        return False
    return bool(getattr(message, "media_url", None) or getattr(message, "media_id", None))


class MessagingService(pydantic.BaseModel):
    _type: ClassVar[str]
    _supported_platforms: ClassVar[list]
    voice_replies_supported: ClassVar[bool] = False
    supports_multimedia: ClassVar[bool] = False
    supported_message_types: ClassVar[list] = []

    def send_text_message(
        self,
        message: str,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        raise NotImplementedError

    def send_voice_message(
        self,
        synthetic_voice: SynthesizedAudio,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        raise NotImplementedError

    def send_template_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs):
        """Internal method for sending template messages. Called by send_text_message()
        when the service window is expired. Should not be called directly from channel code."""
        raise NotImplementedError

    def get_message_audio(self, message: TwilioMessage | WhatsAppMessage):
        """Should return a BytesIO object in .wav format"""
        raise NotImplementedError

    def download_message_media(self, message) -> tuple[bytes, str]:
        """Return (raw_bytes, content_type) for an inbound media message.

        Fetches the media referenced by *message* (image, audio, video, etc.)
        without any format conversion. Callers are responsible for validating
        the content type and size before persisting.
        """
        raise NotImplementedError

    def get_inbound_media(self, message) -> tuple[bytes, str] | None:
        """Return (raw_bytes, content_type) for an inbound media attachment
        (image, document, etc.), or None if the message has no attachment.

        Subclasses override this to apply provider-specific detection (the
        shape of "this message has media" differs between providers) and
        fetch the bytes. Default: no media.
        """
        return None

    def resolve_number(self, number: str) -> str | None:
        """Returns `number` if the number is verified to belong to the account, otherwise return `None`"""
        return number

    supports_webhook_management: ClassVar[bool] = False
    """Whether this service can configure the inbound message webhook at the provider."""

    def set_incoming_webhook(self, extra_data: dict, webhook_url: str):
        """Point the provider's inbound message webhook at `webhook_url`.

        `extra_data` is the channel's `extra_data`; each provider reads whatever fields it needs.
        """
        raise NotImplementedError

    def remove_incoming_webhook(self, extra_data: dict, webhook_url: str):
        """Clear the provider's inbound message webhook if it points at `webhook_url`."""
        raise NotImplementedError


class TwilioSenderWebhookUpdate:
    """Minimal request payload for the WhatsApp Senders API update call.

    The SDK's generated wrapper classes serialize unset fields as JSON nulls, which
    risks clearing the sender's existing configuration/profile. This sends only the
    webhook field. The Senders API `update` accepts any object with a `to_dict` method.
    """

    def __init__(self, webhook: dict):
        self.webhook = webhook

    def to_dict(self):
        return {"webhook": self.webhook}


class HttpMediaDownloadMixin:
    """Shared inbound-media and audio handling for services whose
    ``download_message_media`` fetches bytes over HTTP (Twilio, Turn.io, Meta).

    Subclasses provide the provider-specific ``download_message_media``; this
    mixin reuses it for both the generic inbound-media path and audio
    transcription so detection and error handling stay consistent across
    providers.
    """

    def download_message_media(self, message) -> tuple[bytes, str]:
        # Provided by the concrete service; declared here so the shared helpers
        # below type-check against the contract.
        raise NotImplementedError

    def get_inbound_media(self, message: TwilioMessage | WhatsAppMessage) -> tuple[bytes, str] | None:
        # Voice/audio attachments go through get_message_audio for transcription,
        # never the generic inbound-media path that persists raw bytes.
        if not _has_downloadable_attachment(message):
            return None
        return self.download_message_media(message)

    def get_message_audio(self, message: TwilioMessage | WhatsAppMessage) -> BytesIO:
        try:
            raw_bytes, content_type = self.download_message_media(message)
        except (requests.HTTPError, httpx.HTTPStatusError) as e:
            raise MessageMediaError("Unable to fetch message media") from e

        data = BytesIO(raw_bytes)
        message.cached_media_data = MediaCache(content_type=content_type, data=data)

        # Example header: {'Content-Type': 'audio/ogg'}
        family, sub_type = content_type.split("/", 1)
        if family != "audio":
            raise MessageMediaError(f"Unexpected content-type for audio: {content_type}")
        return audio.convert_audio(data, target_format="wav", source_format=sub_type)


class TwilioService(HttpMediaDownloadMixin, MessagingService):
    _type: ClassVar[str] = "twilio"
    supported_platforms: ClassVar[list] = [ChannelPlatform.WHATSAPP, ChannelPlatform.FACEBOOK]
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]
    supports_multimedia: ClassVar[bool] = True
    max_file_size_mb: ClassVar[int] = 16

    account_sid: str
    auth_token: str

    TWILIO_CHANNEL_PREFIXES: ClassVar[dict[ChannelPlatform, str]] = {
        ChannelPlatform.WHATSAPP: "whatsapp",
        ChannelPlatform.FACEBOOK: "messenger",
    }
    MESSAGE_CHARACTER_LIMIT: int = 1600

    @property
    def voice_replies_supported(self):
        return bool(settings.WHATSAPP_S3_AUDIO_BUCKET)

    @property
    def client(self) -> "Client":
        from twilio.rest import Client  # noqa: PLC0415 - lazy: optional provider dep (twilio SDK)

        return Client(self.account_sid, self.auth_token)

    @property
    def s3_client(self):
        return get_s3_client()

    def _upload_audio_file(self, synthetic_voice: SynthesizedAudio):
        file_path = f"{uuid.uuid4()}.mp3"
        audio_bytes = synthetic_voice.get_audio_bytes(format="mp3")
        self.s3_client.upload_fileobj(
            BytesIO(audio_bytes),
            settings.WHATSAPP_S3_AUDIO_BUCKET,
            file_path,
            ExtraArgs={
                "Expires": datetime.utcnow() + timedelta(minutes=7),
                "Metadata": {
                    "DurationSeconds": str(synthetic_voice.duration),
                },
                "ContentType": "audio/mpeg",
            },
        )
        return self.s3_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.WHATSAPP_S3_AUDIO_BUCKET,
                "Key": file_path,
            },
            ExpiresIn=360,
        )

    def _parse_addressing_params(self, platform: ChannelPlatform, from_: str, to: str):
        prefix = self.TWILIO_CHANNEL_PREFIXES[platform]
        return f"{prefix}:{from_}", f"{prefix}:{to}"

    supports_webhook_management: ClassVar[bool] = True

    def set_incoming_webhook(self, extra_data: dict, webhook_url: str):
        number = extra_data.get("number")
        if not number:
            return
        sender = self._get_whatsapp_sender(number)
        if sender is None:
            raise ValueError(f"No WhatsApp sender found for {number}")
        self._update_sender_webhook(sender, webhook_url)

    def remove_incoming_webhook(self, extra_data: dict, webhook_url: str):
        number = extra_data.get("number")
        if not number:
            return
        sender = self._get_whatsapp_sender(number)
        if sender is None:
            return
        current_url = (sender.webhook or {}).get("callback_url")
        if current_url != webhook_url:
            # never clobber a sender that has been repointed elsewhere
            return
        self._update_sender_webhook(sender, "")

    def _get_whatsapp_sender(self, number: str):
        sender_id = f"whatsapp:{number}"
        for sender in self.client.messaging.v2.channels_senders.list(channel="whatsapp"):
            if sender.sender_id == sender_id:
                return sender
        return None

    def _update_sender_webhook(self, sender, callback_url: str):
        webhook = {key: value for key, value in (sender.webhook or {}).items() if value is not None}
        webhook["callback_url"] = callback_url
        webhook["callback_method"] = "POST"
        self.client.messaging.v2.channels_senders(sender.sid).update(
            messaging_v2_channels_sender_requests_update=TwilioSenderWebhookUpdate(webhook)
        )

    @backoff.on_predicate(
        backoff.constant,
        lambda status: status not in ["delivered", "read"],
        max_time=10,
        interval=2,
        jitter=None,
    )
    def block_until_delivered(self, current_chunk_sid: str) -> str:
        """
        Checks if the current message chunk has been delivered.

        See https://shorturl.at/EZocp for a list of possible statuses.
        """
        message_context = self.client.messages.get(current_chunk_sid)
        message = message_context.fetch()
        return cast(str, message.status)

    def send_text_message(
        self,
        message: str,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        """
        Sends a text message to the user. If the message is too long, it will be split into chunks of
        `MESSAGE_CHARACTER_LIMIT` characters and sent as multiple messages. Sending chunks is done sequentially,
        waiting for the previous chunk to be delivered before sending the next one.

        See https://shorturl.at/valat for more information.
        """
        from_, to = self._parse_addressing_params(platform, from_=from_, to=to)

        chunks = smart_split(message, chars_per_string=self.MESSAGE_CHARACTER_LIMIT)
        num_chunks = len(chunks)
        for message_text in chunks:
            response: MessageInstance = self.client.messages.create(from_=from_, body=message_text, to=to)
            message_id = response.sid

            if num_chunks == 1:
                return

            self.block_until_delivered(message_id)

    def send_voice_message(
        self,
        synthetic_voice: SynthesizedAudio,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        from_, to = self._parse_addressing_params(platform, from_=from_, to=to)

        public_url = self._upload_audio_file(synthetic_voice)
        self.client.messages.create(from_=from_, to=to, media_url=[public_url])

    def download_message_media(self, message: TwilioMessage) -> tuple[bytes, str]:
        """Fetch raw bytes and content type for any inbound Twilio media (image, audio, etc.)."""
        if not message.media_url:
            raise ValueError("Cannot download Twilio media: message.media_url is empty")
        auth = (self.account_sid, self.auth_token)
        response = httpx.get(message.media_url, auth=auth, follow_redirects=True, timeout=MEDIA_DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        return response.content, _normalize_content_type(response.headers.get("Content-Type"))

    def _get_account_numbers(self) -> list[str]:
        """Returns all numbers associated with this client account"""
        return [num.phone_number for num in self.client.incoming_phone_numbers.list() if num.phone_number is not None]

    def resolve_number(self, number: str) -> str | None:
        if settings.DEBUG:
            # The sandbox number doesn't belong to any account, so this check will always fail. For dev purposes
            # let's just always return the number
            return number

        return number if number in self._get_account_numbers() else None

    def send_file_to_user(
        self,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        file: File,
        download_link: str,
        last_activity_at: datetime | None = None,
    ):
        from_, to = self._parse_addressing_params(platform, from_=from_, to=to)
        self.client.messages.create(from_=from_, to=to, body=file.name, media_url=download_link)

    def can_send_file(self, file: File) -> bool:
        return can_send_on_whatsapp(file.content_type or "", file.content_size or 0).supported


class TurnIOService(HttpMediaDownloadMixin, MessagingService):
    _type: ClassVar[str] = "turnio"
    supported_platforms: ClassVar[list] = [ChannelPlatform.WHATSAPP]
    voice_replies_supported: ClassVar[bool] = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]
    supports_multimedia = True

    auth_token: str

    @property
    def client(self) -> "TurnClient":
        from turn import TurnClient  # noqa: PLC0415 - lazy: optional provider dep (Turn SDK)

        return TurnClient(token=self.auth_token)

    def send_text_message(
        self,
        message: str,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        self.client.messages.send_text(to, message)

    def send_voice_message(
        self,
        synthetic_voice: SynthesizedAudio,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        # OGG must use the opus codec: https://whatsapp.turn.io/docs/api/media#uploading-media
        voice_audio_bytes = synthetic_voice.get_audio_bytes(format="ogg", codec="libopus")
        audio_file = BytesIO(voice_audio_bytes)
        audio_file.name = "voice_message.ogg"

        self.client.messages.send_media(
            whatsapp_id=to, file=audio_file, content_type="audio/ogg", media_type="audio", caption=None
        )

    def download_message_media(self, message: WhatsAppMessage) -> tuple[bytes, str]:
        """Fetch raw bytes and content type for any inbound Turn.io media (image, audio, etc.).

        Prefers ``message.media_url`` (direct download) when present; otherwise
        falls back to resolving via the Turn SDK's media_id endpoint.
        """
        if message.media_url:
            response = httpx.get(
                message.media_url,
                headers={"Authorization": f"Bearer {self.auth_token}"},
                follow_redirects=True,
                timeout=MEDIA_DOWNLOAD_TIMEOUT,
            )
        elif message.media_id:
            response = self.client.media.get_media(message.media_id)
        else:
            raise ValueError("Cannot download Turn.io media: both media_url and media_id are empty")
        response.raise_for_status()
        return response.content, _normalize_content_type(response.headers.get("Content-Type"))

    def can_send_file(self, file: File) -> bool:
        return can_send_on_whatsapp(file.content_type or "", file.content_size or 0).supported

    def send_file_to_user(
        self,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        file: File,
        download_link: str,
        last_activity_at: datetime | None = None,
    ):
        mime_type = file.content_type

        if mime_type.startswith("image/"):
            media_type = "image"
        elif mime_type.startswith("video/"):
            media_type = "video"
        elif mime_type.startswith("audio/"):
            media_type = "audio"
        else:
            media_type = "document"

        with file.file.open("rb") as file_obj:
            message_id = self.client.messages.send_media(
                whatsapp_id=to,
                file=file_obj,
                content_type=mime_type,
                media_type=media_type,
                caption=None,
            )

        return message_id


class SureAdhereService(MessagingService):
    _type: ClassVar[str] = "sureadhere"
    supported_platforms: ClassVar[list] = [ChannelPlatform.SUREADHERE]
    voice_replies_supported: ClassVar[bool] = False
    supported_message_types = [MESSAGE_TYPES.TEXT]

    client_id: str
    client_secret: str
    client_scope: str
    base_url: str
    auth_url: str

    def get_access_token(self):
        auth_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": self.client_scope,
        }
        response = httpx.post(self.auth_url, data=auth_data)
        response.raise_for_status()
        return response.json()["access_token"]

    def send_text_message(
        self,
        message: str,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        access_token = self.get_access_token()
        send_msg_url = urljoin(self.base_url, "/treatment/external/send-msg")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
        data = {"patient_Id": to, "message_Body": message}
        response = httpx.post(send_msg_url, headers=headers, json=data)
        response.raise_for_status()


class MetaCloudAPIService(HttpMediaDownloadMixin, MessagingService):
    _type: ClassVar[str] = "meta_cloud_api"
    supported_platforms: ClassVar[list] = [ChannelPlatform.WHATSAPP]
    voice_replies_supported: ClassVar[bool] = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]
    supports_multimedia: ClassVar[bool] = True
    access_token: str
    business_id: str
    app_secret: str = ""
    verify_token: str = ""
    template_language_code: str = "en"

    META_API_BASE_URL: ClassVar[str] = "https://graph.facebook.com/v25.0"
    META_API_TIMEOUT: ClassVar[int] = 30
    WHATSAPP_CHARACTER_LIMIT: ClassVar[int] = 4096
    SERVICE_WINDOW_HOURS: ClassVar[int] = 24
    TEMPLATE_NAME: ClassVar[str] = "new_bot_message"
    # allow 50 characters for the template message without the bot message. 1024 - 100
    TEMPLATE_MESSAGE_CHAR_LIMIT: ClassVar[int] = 924

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def resolve_number(self, number: str) -> str | None:
        """Look up the phone number ID for the given E.164 phone number
        using the WhatsApp Business Account Phone Number Management API."""
        url = f"{self.META_API_BASE_URL}/{self.business_id}/phone_numbers"
        response = httpx.get(
            url, headers=self._headers, params={"fields": "id,display_phone_number"}, timeout=self.META_API_TIMEOUT
        )
        response.raise_for_status()
        for entry in response.json().get("data", []):
            display = entry.get("display_phone_number", "")
            try:
                parsed = phonenumbers.parse(display)
                normalized = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            except phonenumbers.NumberParseException:
                continue
            if normalized == number:
                return entry["id"]
        return None

    def _is_within_service_window(self, last_activity_at: datetime | None) -> bool:
        """Check if the last user activity is within the WhatsApp 24-hour service window.
        Returns False if last_activity_at is None (no activity = outside window, require template).
        """
        if last_activity_at is None:
            return False
        return (timezone.now() - last_activity_at) < timedelta(hours=self.SERVICE_WINDOW_HOURS)

    def _split_template_message(self, message: str) -> list[str]:
        """Split a message into chunks that fit within the template parameter limit,
        splitting at word boundaries to avoid cutting words."""
        return [chunk for chunk in smart_split(message, chars_per_string=self.TEMPLATE_MESSAGE_CHAR_LIMIT) if chunk]

    def send_template_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs):
        """Send a WhatsApp template message using the TEMPLATE_NAME template.

        Raises ServiceWindowExpiredException if the template is not found on Meta's side,
        prompting the operator to configure it in their Meta Business account.
        """
        url = f"{self.META_API_BASE_URL}/{from_}/messages"
        chunks = self._split_template_message(message)
        for chunk in chunks:
            data = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "template",
                "template": {
                    "name": self.TEMPLATE_NAME,
                    "language": {"code": self.template_language_code},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "parameter_name": "bot_message", "text": chunk}],
                        }
                    ],
                },
            }
            response = httpx.post(url, headers=self._headers, json=data, timeout=self.META_API_TIMEOUT)
            if response.status_code == 404:
                logger.warning(
                    "Template message '%s' not found on Meta's API. Response: %s",
                    self.TEMPLATE_NAME,
                    response.text,
                )
                raise ServiceWindowExpiredException(
                    f"The 24-hour service window has expired and the '{self.TEMPLATE_NAME}' template was not found. "
                    f"Please configure the '{self.TEMPLATE_NAME}' template in your Meta Business account."
                )
            response.raise_for_status()

    def send_text_message(
        self,
        message: str,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        if not self._is_within_service_window(last_activity_at):
            logger.info("Service window expired, sending template message instead of text")
            return self.send_template_message(message=message, from_=from_, to=to, platform=platform)

        url = f"{self.META_API_BASE_URL}/{from_}/messages"
        chunks = smart_split(message, chars_per_string=self.WHATSAPP_CHARACTER_LIMIT)
        for chunk in chunks:
            data = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": chunk},
            }
            response = httpx.post(url, headers=self._headers, json=data, timeout=self.META_API_TIMEOUT)
            response.raise_for_status()

    def send_voice_message(
        self,
        synthetic_voice: SynthesizedAudio,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        **kwargs,
    ):
        if not self._is_within_service_window(last_activity_at):
            logger.info("Service window expired, cannot send voice message via template")
            raise ServiceWindowExpiredException("Service window expired, voice messages cannot be sent via template")

        voice_audio_bytes = synthetic_voice.get_audio_bytes(format="ogg", codec="libopus")
        media_id = self._upload_media(from_, voice_audio_bytes, mime_type="audio/ogg", filename="audio.ogg")

        url = f"{self.META_API_BASE_URL}/{from_}/messages"
        data = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "audio",
            "audio": {"id": media_id},
        }
        response = httpx.post(url, headers=self._headers, json=data, timeout=self.META_API_TIMEOUT)
        response.raise_for_status()

    def _upload_media(
        self, phone_number_id: str, file_data: bytes | IO[bytes], mime_type: str, filename: str = "upload"
    ) -> str:
        url = f"{self.META_API_BASE_URL}/{phone_number_id}/media"
        file_obj = BytesIO(file_data) if isinstance(file_data, bytes) else file_data
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {self.access_token}"},
            data={"messaging_product": "whatsapp", "type": mime_type},
            files={"file": (filename, file_obj, mime_type)},
            timeout=self.META_API_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["id"]

    def send_typing_indicator(self, from_: str, message_id: str):
        """Send a typing indicator to the user.

        Marks the incoming message as read and sends a typing indicator in a single API call.
        See https://developers.facebook.com/documentation/business-messaging/whatsapp/typing-indicators/
        """
        url = f"{self.META_API_BASE_URL}/{from_}/messages"
        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }
        httpx.post(url, headers=self._headers, json=data, timeout=self.META_API_TIMEOUT)

    def can_send_file(self, file: File) -> bool:
        return can_send_on_whatsapp(file.content_type or "", file.content_size or 0).supported

    def send_file_to_user(
        self,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        file: File,
        download_link: str,
        last_activity_at: datetime | None = None,
    ):
        if not self._is_within_service_window(last_activity_at):
            raise ServiceWindowExpiredException("Service window expired, file messages cannot be sent via template")

        mime_type = file.content_type

        if mime_type.startswith("image/"):
            media_type = "image"
        elif mime_type.startswith("video/"):
            media_type = "video"
        elif mime_type.startswith("audio/"):
            media_type = "audio"
        else:
            media_type = "document"

        with file.file.open("rb") as file_obj:
            media_id = self._upload_media(from_, file_obj, mime_type=mime_type, filename=file.name)

        url = f"{self.META_API_BASE_URL}/{from_}/messages"
        data = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": media_type,
            media_type: {"id": media_id},
        }
        response = httpx.post(url, headers=self._headers, json=data, timeout=self.META_API_TIMEOUT)
        response.raise_for_status()

    def download_message_media(self, message: WhatsAppMessage) -> tuple[bytes, str]:
        """Fetch raw bytes + content type for any inbound Meta media.

        Prefers ``message.media_url`` (direct download); otherwise resolves the
        URL from media_id via the Meta Media API and fetches that.
        """
        if message.media_url:
            media_url = message.media_url
        elif message.media_id:
            media_url = self._get_media_url(message.media_id)
        else:
            raise ValueError("Cannot download Meta media: both media_url and media_id are empty")
        response = httpx.get(
            media_url,
            headers=self._headers,
            follow_redirects=True,
            timeout=self.META_API_TIMEOUT,
        )
        response.raise_for_status()
        return response.content, _normalize_content_type(response.headers.get("Content-Type"))

    def _get_media_url(self, media_id: str) -> str:
        url = f"{self.META_API_BASE_URL}/{media_id}"
        response = httpx.get(url, headers=self._headers, timeout=self.META_API_TIMEOUT)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise MessageMediaError("Unable to resolve media URL") from e
        return response.json()["url"]


class SlackService(MessagingService):
    _type: ClassVar[str] = "slack"
    supported_platforms: ClassVar[list] = [ChannelPlatform.SLACK]
    voice_replies_supported: ClassVar[bool] = False
    supported_message_types = [MESSAGE_TYPES.TEXT]

    slack_team_id: str
    slack_installation_id: int
    _client: "WebClient | None" = pydantic.PrivateAttr(default=None)

    def send_text_message(
        self,
        message: str,
        from_: str,
        to: str,
        platform: ChannelPlatform,
        last_activity_at: datetime | None = None,
        thread_ts: str | None = None,
        **kwargs,
    ):
        self.client.chat_postMessage(
            channel=to,
            text=message,
            thread_ts=thread_ts,
        )

    @property
    def client(self) -> "WebClient":
        if not self._client:
            from apps.slack.client import get_slack_client  # noqa: PLC0415 - lazy: optional slack_sdk/slack_bolt deps

            self._client = get_slack_client(self.slack_installation_id)
        return self._client

    @client.setter
    def client(self, value: "WebClient"):
        self._client = value

    def iter_channels(self):
        for page in self.client.conversations_list():
            yield from page["channels"]

    def get_channel_by_name(self, name):
        for channel in self.iter_channels():
            if channel["name"] == name:
                return channel

    def join_channel(self, channel_id: str):
        from slack_sdk.errors import SlackApiError  # noqa: PLC0415 - lazy: optional provider dep (slack_sdk)

        try:
            self.client.conversations_info(channel=channel_id)
        except SlackApiError as e:
            message = "Error joining slack channel"
            logger.exception(message)
            raise ServiceProviderConfigError(self._type, message) from e

        self.client.conversations_join(channel=channel_id)

    def send_file_message(self, file: File, to: str, thread_ts: str):
        file_bytes = BytesIO(file.file.read())
        file_bytes.seek(0)

        self.client.files_upload_v2(
            channels=to,
            file=file_bytes,
            filename=file.name,
            thread_ts=thread_ts,
            title=file.name,
        )
