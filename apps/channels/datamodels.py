from typing import Optional

from pydantic import BaseModel, Field, validator
from pydantic.dataclasses import dataclass

from apps.chat.channels import MESSAGE_TYPES


class WebMessage(BaseModel):
    """
    A wrapper class for user messages coming from the UI. It's easier to pass this object to the WebChannel
    and expose some attributes/methods to access chat specific data from the message. This follows a similar
    pattern then that of other channels
    """

    message_text: str
    chat_id: int

    @property
    def content(self) -> str:
        return self.message_text


class WhatsappMessage(BaseModel):
    """
    A wrapper class for user messages coming from the whatsapp
    """

    from_number: str = Field(alias="From")  # `from` is a reserved keyword
    to_number: str = Field(alias="To")
    body: str = Field(alias="Body")
    content_type: MESSAGE_TYPES = Field(default=MESSAGE_TYPES.TEXT, alias="MediaContentType0")
    media_url: Optional[str] = Field(default=None, alias="MediaUrl0")

    @validator("to_number", "from_number", pre=True)
    def strip_prefix(cls, value):
        return value.split("whatsapp:")[1]

    @validator("content_type", pre=True)
    def determine_content_type(cls, value):
        if not value:
            return MESSAGE_TYPES.TEXT
        if value and value == "audio/ogg":
            return MESSAGE_TYPES.VOICE

    @property
    def chat_id(self) -> str:
        return self.from_number

    @property
    def message_text(self) -> str:
        return self.body


class FacebookMessage(BaseModel):
    """
    A wrapper class for user messages coming from Facebook
    """

    page_id: str = Field()
    user_id: str = Field()
    message_text: Optional[str] = Field()
    content_type: MESSAGE_TYPES = Field(default=MESSAGE_TYPES.TEXT)
    media_url: Optional[str] = None

    @validator("content_type", pre=True)
    def determine_content_type(cls, value):
        if not value:
            return MESSAGE_TYPES.TEXT
        if value and value == "audio":
            return MESSAGE_TYPES.VOICE

    @property
    def chat_id(self) -> str:
        return self.user_id
