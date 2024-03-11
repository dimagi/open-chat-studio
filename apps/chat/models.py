from enum import StrEnum
from urllib.parse import quote

from django.conf import settings
from django.db import models
from django.utils.functional import classproperty
from langchain.schema import BaseMessage, messages_from_dict

from apps.teams.models import BaseTeamModel
from apps.users.models import CustomUser
from apps.utils.models import BaseModel


class Chat(BaseTeamModel):
    """
    A chat instance.
    """

    class MetadataKeys(StrEnum):
        OPENAI_THREAD_ID = "openai_thread_id"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    # must match or be greater than experiment name field
    name = models.CharField(max_length=128, default="Unnamed Chat")
    metadata = models.JSONField(default=dict)

    def add_tags(self, tags: list[str], added_by: CustomUser):
        self.tags.add(tags, through_defaults={"team": self.team, "user": added_by})

    def get_metadata(self, key: MetadataKeys):
        return self.metadata.get(key, None)

    def set_metadata(self, key: MetadataKeys, value, commit=True):
        self.metadata[key] = value
        if commit:
            self.save()

    def get_langchain_messages(self) -> list[BaseMessage]:
        return messages_from_dict([m.to_langchain_dict() for m in self.messages.all()])

    def get_langchain_messages_until_summary(self) -> list[BaseMessage]:
        messages = []
        for message in self.messages.order_by("-created_at").iterator(100):
            messages.append(message.to_langchain_dict())
            if message.summary:
                messages.append(message.summary_to_langchain_dict())
                break

        return messages_from_dict(list(reversed(messages)))


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
    summary = models.TextField(  # noqa DJ001
        null=True, blank=True, help_text="The summary of the conversation up to this point (not including this message)"
    )

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
        return self._get_langchain_dict(self.content, self.message_type)

    def summary_to_langchain_dict(self) -> dict:
        return self._get_langchain_dict(self.summary, ChatMessageType.SYSTEM)

    def _get_langchain_dict(self, content, message_type):
        return {
            "type": message_type,
            "data": {
                "content": content,
                "additional_kwargs": {
                    "id": self.id,
                },
            },
        }
