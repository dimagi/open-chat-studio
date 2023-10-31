from typing import List
from urllib.parse import quote

from django.conf import settings
from django.db import models
from django.utils.functional import classproperty
from langchain.schema import BaseMessage, messages_from_dict

from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel


class Chat(BaseTeamModel):
    """
    A chat instance.
    """

    # tbd what goes in here
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100, default="Unnamed Chat")

    def get_langchain_messages(self) -> List[BaseMessage]:
        return messages_from_dict([m.to_langchain_dict() for m in self.messages.all()])


class ChatMessageType(models.TextChoices):
    #  these must correspond to the langchain values
    HUMAN = "human", "Human"
    AI = "ai", "AI"
    SYSTEM = "system", "System"

    @classproperty
    def safety_layer_choices(cls):
        return (
            (choice[0], f"{choice[1]} messages")
            for choice in ChatMessageType.choices
            if choice[0] != ChatMessageType.SYSTEM
        )


class ChatMessage(BaseModel):
    """
    A message in a chat. Analogous to the BaseMessage class in langchain.
    """

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="messages")
    message_type = models.CharField(max_length=10, choices=ChatMessageType.choices)
    content = models.TextField()
    # todo: additional_kwargs? dict

    class Meta:
        ordering = ["created_at"]

    @property
    def is_ai_message(self):
        return self.message_type == ChatMessageType.AI

    @property
    def is_human_message(self):
        return self.message_type == ChatMessageType.HUMAN

    @property
    def created_at_datetime(self):
        return quote(self.created_at.isoformat())

    def to_langchain_dict(self) -> dict:
        return {
            "type": self.message_type,
            "data": {
                "content": self.content,
            },
        }
