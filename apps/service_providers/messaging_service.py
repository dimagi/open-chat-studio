import uuid
from datetime import datetime, timedelta
from io import BytesIO
from typing import ClassVar

import boto3
import pydantic
import requests
from botocore.client import Config
from django.conf import settings
from turn import TurnClient
from twilio.rest import Client

from apps.channels import audio
from apps.channels.datamodels import TurnWhatsappMessage, TwilioMessage
from apps.channels.models import ChannelPlatform
from apps.chat.channels import MESSAGE_TYPES


class MessagingService(pydantic.BaseModel):
    _type: ClassVar[str]
    _supported_platforms: ClassVar[list]
    voice_replies_supported: ClassVar[bool] = False
    supported_message_types: ClassVar[list] = []

    def send_whatsapp_text_message(self, message: str, from_number: str, to_number):
        raise NotImplementedError

    def send_whatsapp_voice_message(self, media_url: str, from_number: str, to_number):
        raise NotImplementedError

    def get_message_audio(self, message: TwilioMessage | TurnWhatsappMessage):
        """Should return a BytesIO object in .wav format"""
        raise NotImplementedError


class TwilioService(MessagingService):
    _type: ClassVar[str] = "twilio"
    supported_platforms: ClassVar[list] = [ChannelPlatform.WHATSAPP]
    voice_replies_supported: ClassVar[bool] = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]

    account_sid: str
    auth_token: str

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

    def send_whatsapp_text_message(self, message: str, from_number: str, to_number):
        self.client.messages.create(from_=f"whatsapp:{from_number}", body=message, to=f"whatsapp:{to_number}")

    def send_whatsapp_voice_message(self, voice_audio: BytesIO, duration: int, from_number: str, to_number):
        file_path = f"{uuid.uuid4()}.mp3"
        audio_bytes = voice_audio.getvalue()
        self.s3_client.upload_fileobj(
            BytesIO(audio_bytes),
            settings.WHATSAPP_S3_AUDIO_BUCKET,
            file_path,
            ExtraArgs={
                "Expires": datetime.utcnow() + timedelta(minutes=7),
                "Metadata": {
                    "DurationSeconds": str(duration),
                },
                "ContentType": "audio/mpeg",
            },
        )
        public_url = self.s3_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.WHATSAPP_S3_AUDIO_BUCKET,
                "Key": file_path,
            },
            ExpiresIn=360,
        )
        self.client.messages.create(from_=f"whatsapp:{from_number}", to=f"whatsapp:{to_number}", media_url=[public_url])

    def get_message_audio(self, message: TwilioMessage) -> BytesIO:
        auth = (self.account_sid, self.auth_token)
        ogg_audio = BytesIO(requests.get(message.media_url, auth=auth).content)
        return audio.convert_audio(ogg_audio, target_format="wav", source_format="ogg")


class TurnIOService(MessagingService):
    _type: ClassVar[str] = "turnio"
    supported_platforms: ClassVar[list] = [ChannelPlatform.WHATSAPP]
    voice_replies_supported: ClassVar[bool] = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]

    auth_token: str

    @property
    def client(self) -> TurnClient:
        return TurnClient(token=self.auth_token)

    def send_whatsapp_text_message(self, message: str, from_number: str, to_number):
        self.client.messages.send_text(to_number, message)

    def send_whatsapp_voice_message(self, voice_audio: BytesIO, duration: int, from_number: str, to_number: str):
        from apps.channels.audio import convert_audio

        # OGG must use the opus codec: https://whatsapp.turn.io/docs/api/media#uploading-media
        voice_audio = convert_audio(voice_audio, target_format="ogg", source_format="mp3", codec="libopus")
        media_id = self.client.media.upload_media(voice_audio.read(), content_type="audio/ogg")
        self.client.messages.send_audio(whatsapp_id=to_number, media_id=media_id)

    def get_message_audio(self, message: TurnWhatsappMessage) -> BytesIO:
        response = self.client.media.get_media(message.media_id)
        ogg_audio = BytesIO(response.content)
        return audio.convert_audio(ogg_audio, target_format="wav", source_format="ogg")
