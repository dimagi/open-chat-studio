from typing import List
from urllib.parse import quote

from django.conf import settings
from django.db import models
from langchain.schema import BaseMessage, messages_from_dict

from apps.utils.models import BaseModel


class Chat(BaseModel):
    """
    A chat instance.
    """

    # tbd what goes in here
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100, default="Unnamed Chat")

    def get_langchain_messages(self) -> List[BaseMessage]:
        return messages_from_dict([m.to_langchain_dict() for m in self.messages.all()])


class ChatMessage(BaseModel):
    """
    A message in a chat. Analogous to the BaseMessage class in langchain.
    """

    # these must correspond to the langchain values
    MESSAGE_TYPE_CHOICES = (
        ("human", "Human"),
        ("ai", "AI"),
        ("system", "System"),
    )
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="messages")
    message_type = models.CharField(max_length=10, choices=MESSAGE_TYPE_CHOICES)
    content = models.TextField()
    # todo: additional_kwargs? dict

    class Meta:
        ordering = ["created_at"]

    @property
    def is_ai_message(self):
        return self.message_type == "ai"

    @property
    def is_human_message(self):
        return self.message_type == "human"

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


class FutureMessage(BaseModel):
    """
    A message that will be sent in the future.
    """

    message = models.CharField(null=False, blank=False)
    due_at = models.DateTimeField()
    interval_minutes = models.IntegerField(null=True, blank=True)
    experiment_session = models.ForeignKey(
        "experiments.ExperimentSession", on_delete=models.CASCADE, related_name="future_messages"
    )
    end_date = models.DateTimeField()
    resolved = models.BooleanField(default=False)
