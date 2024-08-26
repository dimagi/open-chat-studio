import logging
import uuid
from datetime import datetime, timedelta
from functools import cached_property
from io import BytesIO
from typing import ClassVar
from urllib.parse import urljoin

import boto3
import pydantic
import requests
from botocore.client import Config
from django.conf import settings
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from telebot.util import smart_split
from turn import TurnClient
from twilio.rest import Client

from apps.channels import audio
from apps.channels.datamodels import TurnWhatsappMessage, TwilioMessage
from apps.channels.models import ChannelPlatform
from apps.chat.channels import MESSAGE_TYPES
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.speech_service import SynthesizedAudio

logger = logging.getLogger(__name__)


class MessagingService(pydantic.BaseModel):
    _type: ClassVar[str]
    _supported_platforms: ClassVar[list]
    voice_replies_supported: ClassVar[bool] = False
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

    def send_text_message(self, message: str, from_: str, to: str, platform: ChannelPlatform, **kwargs):
        prefix = self.TWILIO_CHANNEL_PREFIXES[platform]
        for message_text in smart_split(message, chars_per_string=self.MESSAGE_CHARACTER_LIMIT):
            self.client.messages.create(from_=f"{prefix}:{from_}", body=message_text, to=f"{prefix}:{to}")

    def send_voice_message(
        self, synthetic_voice: SynthesizedAudio, from_: str, to: str, platform: ChannelPlatform, **kwargs
    ):
        prefix = self.TWILIO_CHANNEL_PREFIXES[platform]
        public_url = self._upload_audio_file(synthetic_voice)
        self.client.messages.create(from_=f"{prefix}:{from_}", to=f"{prefix}:{to}", media_url=[public_url])

    def get_message_audio(self, message: TwilioMessage) -> BytesIO:
        auth = (self.account_sid, self.auth_token)
        response = requests.get(message.media_url, auth=auth)
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


class TurnIOService(MessagingService):
    _type: ClassVar[str] = "turnio"
    supported_platforms: ClassVar[list] = [ChannelPlatform.WHATSAPP]
    voice_replies_supported: ClassVar[bool] = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]

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
        media_id = self.client.media.upload_media(voice_audio_bytes, content_type="audio/ogg")
        self.client.messages.send_audio(whatsapp_id=to, media_id=media_id)

    def get_message_audio(self, message: TurnWhatsappMessage) -> BytesIO:
        response = self.client.media.get_media(message.media_id)
        ogg_audio = BytesIO(response.content)
        return audio.convert_audio(ogg_audio, target_format="wav", source_format="ogg")


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
        response = requests.post(self.auth_url, data=auth_data)
        response.raise_for_status()
        return response.json()["access_token"]

    def send_text_message(self, message: str, to: str, platform: ChannelPlatform, from_: str = None):
        access_token = self.get_access_token()
        send_msg_url = urljoin(self.base_url, "/treatment/external/send-msg")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
        data = {"patient_Id": to, "message_Body": message}
        response = requests.post(send_msg_url, headers=headers, json=data)
        response.raise_for_status()


class SlackService(MessagingService):
    _type: ClassVar[str] = "slack"
    supported_platforms: ClassVar[list] = [ChannelPlatform.SLACK]
    voice_replies_supported: ClassVar[bool] = False
    supported_message_types = [MESSAGE_TYPES.TEXT]

    slack_team_id: str
    slack_installation_id: int

    def send_text_message(
        self, message: str, from_: str, to: str, platform: ChannelPlatform, thread_ts: str = None, **kwargs
    ):
        self.client.chat_postMessage(
            channel=to,
            text=message,
            thread_ts=thread_ts,
        )

    @cached_property
    def client(self) -> WebClient:
        from apps.slack.client import get_slack_client

        return get_slack_client(self.slack_installation_id)

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
