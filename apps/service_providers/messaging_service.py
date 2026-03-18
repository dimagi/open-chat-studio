import logging
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from typing import TYPE_CHECKING, ClassVar, cast
from urllib.parse import urljoin

import backoff
import httpx
import phonenumbers
import pydantic
import requests
from django.conf import settings
from telebot.util import smart_split

if TYPE_CHECKING:
    from slack_sdk import WebClient
    from turn import TurnClient
    from twilio.rest import Client
    from twilio.rest.api.v2010.account.message import MessageInstance

from apps.channels import audio
from apps.channels.datamodels import MediaCache, TurnWhatsappMessage, TwilioMessage
from apps.channels.models import ChannelPlatform
from apps.chat.channels import MESSAGE_TYPES
from apps.files.models import File
from apps.service_providers import supported_mime_types
from apps.service_providers.exceptions import AudioConversionError, ServiceProviderConfigError
from apps.service_providers.speech_service import SynthesizedAudio

logger = logging.getLogger("ocs.messaging")


class MessagingService(pydantic.BaseModel):
    _type: ClassVar[str]
    _supported_platforms: ClassVar[list]
    voice_replies_supported: ClassVar[bool] = False
    supports_multimedia: ClassVar[bool] = False
    supported_message_types: ClassVar[list] = []

    def send_text_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs):
        raise NotImplementedError

    def send_voice_message(
        self, synthetic_voice: SynthesizedAudio, from_: str, to: str, platform: ChannelPlatform, **kwargs
    ):
        raise NotImplementedError

    def get_message_audio(self, message: TwilioMessage | TurnWhatsappMessage):
        """Should return a BytesIO object in .wav format"""
        raise NotImplementedError

    def resolve_number(self, number: str) -> str | None:
        """Returns `number` if the number is verified to belong to the account, otherwise return `None`"""
        return number


class TwilioService(MessagingService):
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
        import boto3  # noqa: PLC0415 - TID253: heavy lib, slow startup
        from botocore.client import Config  # noqa: PLC0415 - lazy: used with boto3

        return boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION,
            config=Config(signature_version="s3v4"),
        )

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

    def send_text_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs):
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
        **kwargs,
    ):
        from_, to = self._parse_addressing_params(platform, from_=from_, to=to)

        public_url = self._upload_audio_file(synthetic_voice)
        self.client.messages.create(from_=from_, to=to, media_url=[public_url])

    def get_message_audio(self, message: TwilioMessage) -> BytesIO:  # ty: ignore[invalid-method-override]
        auth = (self.account_sid, self.auth_token)
        response = httpx.get(message.media_url, auth=auth, follow_redirects=True)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise AudioConversionError("Unable to fetch message media") from e

        data = BytesIO(response.content)
        content_type = response.headers["Content-Type"]
        message.cached_media_data = MediaCache(content_type=content_type, data=data)

        # Example header: {'Content-Type': 'audio/ogg'}
        family, sub_type = content_type.split("/", 1)
        if family != "audio":
            raise AudioConversionError(f"Unexpected content-type for audio: {content_type}")
        converted = audio.convert_audio(data, target_format="wav", source_format=sub_type)
        data.seek(0)
        return converted

    def _get_account_numbers(self) -> list[str]:
        """Returns all numbers associated with this client account"""
        return [num.phone_number for num in self.client.incoming_phone_numbers.list() if num.phone_number is not None]

    def resolve_number(self, number: str) -> str | None:
        if settings.DEBUG:
            # The sandbox number doesn't belong to any account, so this check will always fail. For dev purposes
            # let's just always return the number
            return number

        return number if number in self._get_account_numbers() else None

    def send_file_to_user(self, from_: str, to: str, platform: ChannelPlatform, file: File, download_link: str):
        from_, to = self._parse_addressing_params(platform, from_=from_, to=to)
        self.client.messages.create(from_=from_, to=to, body=file.name, media_url=download_link)

    def can_send_file(self, file: File) -> bool:
        return file.content_type in supported_mime_types.TWILIO and file.size_mb <= self.max_file_size_mb


class TurnIOService(MessagingService):
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

    def send_text_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs):
        self.client.messages.send_text(to, message)

    def send_voice_message(
        self, synthetic_voice: SynthesizedAudio, from_: str, to: str, platform: ChannelPlatform, **kwargs
    ):
        # OGG must use the opus codec: https://whatsapp.turn.io/docs/api/media#uploading-media
        voice_audio_bytes = synthetic_voice.get_audio_bytes(format="ogg", codec="libopus")
        audio_file = BytesIO(voice_audio_bytes)
        audio_file.name = "voice_message.ogg"

        self.client.messages.send_media(
            whatsapp_id=to, file=audio_file, content_type="audio/ogg", media_type="audio", caption=None
        )

    def get_message_audio(self, message: TurnWhatsappMessage) -> BytesIO:  # ty: ignore[invalid-method-override]
        response = self.client.media.get_media(message.media_id)

        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            raise AudioConversionError("Unable to fetch message media") from e

        data = BytesIO(response.content)
        content_type = response.headers["Content-Type"]
        message.cached_media_data = MediaCache(content_type=content_type, data=data)

        # Example header: {'Content-Type': 'audio/ogg'}
        family, sub_type = content_type.split("/", 1)
        if family != "audio":
            raise AudioConversionError(f"Unexpected content-type for audio: {content_type}")

        return audio.convert_audio(data, target_format="wav", source_format=sub_type)

    def can_send_file(self, file: File) -> bool:
        mime = file.content_type
        size = file.content_size or 0  # in bytes

        if mime is None:
            return False

        if mime.startswith("image/"):
            return size <= 5 * 1024 * 1024  # 5 MB
        elif mime.startswith(("video/", "audio/")):
            return size <= 16 * 1024 * 1024  # 16 MB
        elif mime.startswith("application/"):
            return size <= 100 * 1024 * 1024  # 100 MB
        else:
            return False

    def send_file_to_user(self, from_: str, to: str, platform: ChannelPlatform, file: File, download_link: str):
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

    def send_text_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs):
        access_token = self.get_access_token()
        send_msg_url = urljoin(self.base_url, "/treatment/external/send-msg")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
        data = {"patient_Id": to, "message_Body": message}
        response = httpx.post(send_msg_url, headers=headers, json=data)
        response.raise_for_status()


class MetaCloudAPIService(MessagingService):
    _type: ClassVar[str] = "meta_cloud_api"
    supported_platforms: ClassVar[list] = [ChannelPlatform.WHATSAPP]
    voice_replies_supported: ClassVar[bool] = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]

    access_token: str
    business_id: str
    app_secret: str = ""
    verify_token: str = ""

    META_API_BASE_URL: ClassVar[str] = "https://graph.facebook.com/v25.0"
    META_API_TIMEOUT: ClassVar[int] = 30
    WHATSAPP_CHARACTER_LIMIT: ClassVar[int] = 4096

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

    def send_text_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs):
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
        self, synthetic_voice: SynthesizedAudio, from_: str, to: str, platform: ChannelPlatform, **kwargs
    ):
        voice_audio_bytes = synthetic_voice.get_audio_bytes(format="ogg", codec="libopus")
        media_id = self._upload_media(from_, voice_audio_bytes, mime_type="audio/ogg")

        url = f"{self.META_API_BASE_URL}/{from_}/messages"
        data = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "audio",
            "audio": {"id": media_id},
        }
        response = httpx.post(url, headers=self._headers, json=data, timeout=self.META_API_TIMEOUT)
        response.raise_for_status()

    def _upload_media(self, phone_number_id: str, file_bytes: bytes, mime_type: str) -> str:
        url = f"{self.META_API_BASE_URL}/{phone_number_id}/media"
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {self.access_token}"},
            data={"messaging_product": "whatsapp", "type": mime_type},
            files={"file": ("audio.ogg", BytesIO(file_bytes), mime_type)},
            timeout=self.META_API_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["id"]

    def get_message_audio(self, message: TurnWhatsappMessage) -> BytesIO:  # ty: ignore[invalid-method-override]
        # Step 1: Get the media download URL from Meta's API
        media_url = self._get_media_url(message.media_id)

        # Step 2: Download the audio binary
        response = httpx.get(
            media_url,
            headers=self._headers,
            follow_redirects=True,
            timeout=self.META_API_TIMEOUT,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise AudioConversionError("Unable to fetch message media") from e

        data = BytesIO(response.content)
        content_type = response.headers["Content-Type"]
        message.cached_media_data = MediaCache(content_type=content_type, data=data)

        family, sub_type = content_type.split("/", 1)
        if family != "audio":
            raise AudioConversionError(f"Unexpected content-type for audio: {content_type}")

        converted = audio.convert_audio(data, target_format="wav", source_format=sub_type)
        data.seek(0)
        return converted

    def _get_media_url(self, media_id: str) -> str:
        url = f"{self.META_API_BASE_URL}/{media_id}"
        response = httpx.get(url, headers=self._headers, timeout=self.META_API_TIMEOUT)
        response.raise_for_status()
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
        self, message: str, from_: str, to: str, platform: ChannelPlatform, thread_ts: str | None = None, **kwargs
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
