from pydantic import BaseModel, Field, field_validator

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


class TelegramMessage(BaseModel):
    chat_id: int
    body: str | None
    content_type: MESSAGE_TYPES | None = Field(default=MESSAGE_TYPES.TEXT)
    media_id: str | None
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
            chat_id=update_obj.message.chat.id,
            body=update_obj.message.text,
            content_type=update_obj.message.content_type,
            media_id=update_obj.message.voice.file_id if update_obj.message.content_type == "voice" else None,
            message_id=update_obj.message.message_id,
            content_type_unparsed=update_obj.message.content_type,
        )


class TwilioMessage(BaseModel):
    """
    A wrapper class for user messages coming from the whatsapp
    """

    from_number: str
    to_number: str
    body: str
    content_type: MESSAGE_TYPES | None = Field(default=MESSAGE_TYPES.TEXT)
    media_url: str | None = Field(default=None)
    content_type_unparsed: str | None = Field(default=None)

    @field_validator("to_number", "from_number", mode="before")
    @classmethod
    def strip_prefix(cls, value):
        return value.split("whatsapp:")[1]

    @field_validator("content_type", mode="before")
    @classmethod
    def determine_content_type(cls, value):
        if not value:
            # Normal test messages doesn't have a content type
            return MESSAGE_TYPES.TEXT
        if value and value == "audio/ogg":
            return MESSAGE_TYPES.VOICE

    @property
    def chat_id(self) -> str:
        return self.from_number

    @property
    def message_text(self) -> str:
        return self.body

    @staticmethod
    def parse(message_data: dict) -> "TwilioMessage":
        content_type = message_data.get("MediaContentType0")
        return TwilioMessage(
            from_number=message_data["From"],
            to_number=message_data["To"],
            body=message_data["Body"],
            content_type=content_type,
            media_url=message_data.get("MediaUrl0"),
            content_type_unparsed=content_type,
        )


class TurnWhatsappMessage(BaseModel):
    from_number: str
    to_number: str = Field(default="", required=False)  # This field is needed for the WhatsappChannel
    body: str
    content_type: MESSAGE_TYPES | None = Field(default=MESSAGE_TYPES.TEXT)
    media_id: str | None = Field(default=None)
    content_type_unparsed: str | None = Field(default=None)

    @field_validator("content_type", mode="before")
    @classmethod
    def determine_content_type(cls, value):
        if MESSAGE_TYPES.is_member(value):
            return MESSAGE_TYPES(value)

    @property
    def chat_id(self) -> str:
        return self.from_number

    @property
    def message_text(self) -> str:
        return self.body

    @staticmethod
    def parse(message_data: dict):
        message = message_data["messages"][0]
        message_type = message["type"]
        body = ""
        if message_type == "text":
            body = message["text"]["body"]

        return TurnWhatsappMessage(
            from_number=message_data["contacts"][0]["wa_id"],
            body=body,
            content_type=message_type,
            media_id=message[message_type].get("id"),
            content_type_unparsed=message_type,
        )


class FacebookMessage(BaseModel):
    """
    A wrapper class for user messages coming from Facebook
    """

    page_id: str
    user_id: str
    message_text: str | None
    content_type: MESSAGE_TYPES | None = Field(default=MESSAGE_TYPES.TEXT)
    media_url: str | None = None
    content_type_unparsed: str | None = Field(default=None)

    @field_validator("content_type", mode="before")
    @classmethod
    def determine_content_type(cls, value):
        if not value:
            return MESSAGE_TYPES.TEXT
        if value and value == "audio":
            return MESSAGE_TYPES.VOICE

    @property
    def chat_id(self) -> str:
        return self.user_id

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
            user_id=message_data["sender"]["id"],
            page_id=page_id,
            message_text=message_data["message"].get("text", ""),
            media_url=media_url,
            content_type=content_type,
            content_type_unparsed=content_type,
        )
