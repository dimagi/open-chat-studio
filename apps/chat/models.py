import logging
from enum import StrEnum
from urllib.parse import quote

from django.db import models
from django.utils.functional import classproperty
from langchain.schema import BaseMessage, messages_from_dict

from apps.annotations.models import TaggedModelMixin, UserCommentsMixin
from apps.files.models import File
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel

logger = logging.getLogger(__name__)


class Chat(BaseTeamModel, TaggedModelMixin, UserCommentsMixin):
    """
    A chat instance.
    """

    class MetadataKeys(StrEnum):
        OPENAI_THREAD_ID = "openai_thread_id"

    # must match or be greater than experiment name field
    name = models.CharField(max_length=128, default="Unnamed Chat")
    metadata = models.JSONField(default=dict)

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


class ChatMessage(BaseModel, TaggedModelMixin, UserCommentsMixin):
    """
    A message in a chat. Analogous to the BaseMessage class in langchain.
    """

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="messages")
    message_type = models.CharField(max_length=10, choices=ChatMessageType.choices)
    content = models.TextField()
    summary = models.TextField(  # noqa DJ001
        null=True, blank=True, help_text="The summary of the conversation up to this point (not including this message)"
    )
    metadata = models.JSONField(default=dict)

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

    def to_langchain_message(self) -> BaseMessage:
        return messages_from_dict([self.to_langchain_dict()])[0]

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

    def get_attached_files(self):
        """For display purposes. Returns the tool resource files for which this message has references to. The
        reference will be the file's external id

        Message metadata example:
        {
            "code_interpreter": ["file_id_1", "file_id_2"]
            "file_search": ["file_id_3", "file_id_4"]
        }
        """
        file_ids = []
        file_ids.extend(self.metadata.get("file_search", []))
        file_ids.extend(self.metadata.get("code_interpreter", []))
        if file_ids:
            return File.objects.filter(team=self.chat.team, external_id__in=file_ids)
        return []


class ChatAttachment(BaseModel):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="attachments")
    tool_type = models.CharField(max_length=128)
    files = models.ManyToManyField("files.File", blank=True)
    extra = models.JSONField(default=dict, blank=True)

    @property
    def label(self):
        return self.tool_type.replace("_", " ").title()

    def __str__(self):
        return f"Tool Resources for chat {self.chat.id}: {self.tool_type}"
