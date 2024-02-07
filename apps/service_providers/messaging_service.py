from io import BytesIO
from typing import ClassVar, Union

import pydantic
import requests
from turn import TurnClient
from twilio.rest import Client

from apps.channels import audio
from apps.channels.datamodels import TurnWhatsappMessage, WhatsappMessage
from apps.channels.models import ChannelPlatform


class MessagingService(pydantic.BaseModel):
    _type: ClassVar[str]
    _supported_platforms: ClassVar[list]

    def send_whatsapp_text_message(self, message: str, from_number: str, to_number):
        raise NotImplementedError

    def send_whatsapp_voice_message(self, media_url: str, from_number: str, to_number):
        raise NotImplementedError

    def get_message_audio(self, message: Union[TwilioMessage, TurnWhatsappMessage]):
        """Should return a BytesIO object in .wav format"""
        raise NotImplementedError


class TwilioService(MessagingService):
    _type: ClassVar[str] = "twilio"
    supported_platforms: ClassVar[list] = [ChannelPlatform.WHATSAPP]

    account_sid: str
    auth_token: str

    @property
    def client(self) -> Client:
        return Client(self.account_sid, self.auth_token)

    def send_whatsapp_text_message(self, message: str, from_number: str, to_number):
        self.client.messages.create(from_=f"whatsapp:{from_number}", body=message, to=f"whatsapp:{to_number}")

    def send_whatsapp_voice_message(self, media_url: str, from_number: str, to_number):
        self.client.messages.create(from_=f"whatsapp:{from_number}", to=f"whatsapp:{to_number}", media_url=[media_url])

    def get_message_audio(self, message: WhatsappMessage) -> BytesIO:
        auth = (self.account_sid, self.auth_token)
        ogg_audio = BytesIO(requests.get(message.media_url, auth=auth).content)
        return audio.convert_audio_to_wav(ogg_audio)


class TurnIOService(MessagingService):
    _type: ClassVar[str] = "turnio"
    supported_platforms: ClassVar[list] = [ChannelPlatform.WHATSAPP]

    auth_token: str

    @property
    def client(self) -> Client:
        return TurnClient(token=self.auth_token)

    def send_whatsapp_text_message(self, message: str, from_number: str, to_number):
        self.client.messages.send_text(to_number, message)

    def send_whatsapp_voice_message(self, media_url: str, from_number: str, to_number):
        # TODO
        pass

    def get_message_audio(self, message: TurnWhatsappMessage) -> BytesIO:
        response = self.client.media.get_media(message.media_id)
        ogg_audio = BytesIO(response.content)
        return audio.convert_audio_to_wav(ogg_audio)
