import logging
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from typing import ClassVar
from urllib.parse import urljoin

import backoff
import boto3
import httpx
import pydantic
from botocore.client import Config
from django.conf import settings
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from telebot.util import smart_split
from turn import TurnClient
from twilio.rest import Client
from twilio.rest.api.v2010.account.message import MessageContext, MessageInstance

from apps.channels import audio
from apps.channels.datamodels import TurnWhatsappMessage, TwilioMessage
from apps.channels.models import ChannelPlatform
from apps.chat.channels import MESSAGE_TYPES
from apps.files.models import File
from apps.service_providers import supported_mime_types
from apps.service_providers.exceptions import ServiceProviderConfigError
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

    def is_valid_number(self, number: str) -> bool:
        """Returns False if `number` does not belong to this account. Returns `True` by default so that this
        doesn't prevent users from adding numbers if we cannot check the account.
        """
        return True


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
    def client(self) -> Client:
        return Client(self.account_sid, self.auth_token)

    @property
    def s3_client(self):
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
        lambda status: status not in [MessageInstance.Status.DELIVERED, MessageInstance.Status.READ],
        max_time=10,
        interval=2,
        jitter=None,
    )
    def block_until_delivered(self, current_chunk_sid: str) -> bool:
        """
        Checks if the current message chunk has been delivered.

        See https://shorturl.at/EZocp for a list of possible statuses.
        """
        message_context: MessageContext = self.client.messages.get(current_chunk_sid)
        message = message_context.fetch()
        return message.status

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

    def get_message_audio(self, message: TwilioMessage) -> BytesIO:
        auth = (self.account_sid, self.auth_token)
        response = httpx.get(message.media_url, auth=auth, follow_redirects=True)
        # Example header: {'Content-Type': 'audio/ogg'}
        content_type = response.headers["Content-Type"].split("/")[1]
        return audio.convert_audio(BytesIO(response.content), target_format="wav", source_format=content_type)

    def _get_account_numbers(self) -> list[str]:
        """Returns all numbers associated with this client account"""
        return [num.phone_number for num in self.client.incoming_phone_numbers.list()]

    def is_valid_number(self, number: str) -> bool:
        if settings.DEBUG:
            # The sandbox number doesn't belong to any account, so this check will always fail. For dev purposes
            # let's just always return True
            return True

        return number in self._get_account_numbers()

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
    def client(self) -> TurnClient:
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

    def get_message_audio(self, message: TurnWhatsappMessage) -> BytesIO:
        response = self.client.media.get_media(message.media_id)
        ogg_audio = BytesIO(response.content)
        return audio.convert_audio(ogg_audio, target_format="wav", source_format="ogg")

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

    def send_text_message(self, message: str, to: str, platform: ChannelPlatform, from_: str = None):
        access_token = self.get_access_token()
        send_msg_url = urljoin(self.base_url, "/treatment/external/send-msg")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
        data = {"patient_Id": to, "message_Body": message}
        response = httpx.post(send_msg_url, headers=headers, json=data)
        response.raise_for_status()


class SlackService(MessagingService):
    _type: ClassVar[str] = "slack"
    supported_platforms: ClassVar[list] = [ChannelPlatform.SLACK]
    voice_replies_supported: ClassVar[bool] = False
    supported_message_types = [MESSAGE_TYPES.TEXT]

    slack_team_id: str
    slack_installation_id: int
    _client: WebClient | None = pydantic.PrivateAttr(default=None)

    def send_text_message(
        self, message: str, from_: str, to: str, platform: ChannelPlatform, thread_ts: str = None, **kwargs
    ):
        self.client.chat_postMessage(
            channel=to,
            text=message,
            thread_ts=thread_ts,
        )

    @property
    def client(self) -> WebClient:
        if not self._client:
            from apps.slack.client import get_slack_client

            self._client = get_slack_client(self.slack_installation_id)
        return self._client

    @client.setter
    def client(self, value: WebClient):
        self._client = value

    def iter_channels(self):
        for page in self.client.conversations_list():
            yield from page["channels"]

    def get_channel_by_name(self, name):
        for channel in self.iter_channels():
            if channel["name"] == name:
                return channel

    def join_channel(self, channel_id: str):
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
