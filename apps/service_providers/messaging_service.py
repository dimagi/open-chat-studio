from io import BytesIO

import pydantic
from langchain.chat_models import AzureChatOpenAI, ChatOpenAI
from langchain.chat_models.base import BaseChatModel


class MessagingService(pydantic.BaseModel):
    _type: str

    def send_whatsapp_message(self):
        raise NotImplementedError
