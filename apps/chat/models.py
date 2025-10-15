from enum import StrEnum
from urllib.parse import quote

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q
from django.utils.functional import classproperty
from langchain_core.messages import BaseMessage, messages_from_dict

from apps.annotations.models import Tag, TagCategories, TaggedModelMixin, UserCommentsMixin
from apps.files.models import File
from apps.teams.models import BaseTeamModel
from apps.utils.models import BaseModel


class Chat(BaseTeamModel, TaggedModelMixin, UserCommentsMixin):
    """
    A chat instance.
    """

    class MetadataKeys(StrEnum):
        OPENAI_THREAD_ID = "openai_thread_id"
        EXPERIMENT_VERSION = "experiment_version"
        EMBED_SOURCE = "embed_source"

    # must match or be greater than experiment name field
    name = models.CharField(max_length=128, default="Unnamed Chat")
    translated_languages = ArrayField(
        models.CharField(max_length=3),
        default=list,
        blank=True,
        null=True,
        help_text="List of language codes for which translated text is available",
    )
    metadata = models.JSONField(default=dict)

    @property
    def embed_source(self):
        return self.metadata.get(Chat.MetadataKeys.EMBED_SOURCE)

    def get_metadata(self, key: MetadataKeys):
        return self.metadata.get(key, None)

    def set_metadata(self, key: MetadataKeys, value, commit=True):
        self.metadata[key] = value
        if commit:
            self.save()

    def get_langchain_messages(self) -> list[BaseMessage]:
        return messages_from_dict([m.to_langchain_dict() for m in self.messages.all()])

    def get_langchain_messages_until_marker(self, marker: str) -> list[BaseMessage]:
        """Fetch messages from the database until a marker is found. The marker must be one of the
        PipelineChatHistoryModes values.
        """
        from apps.pipelines.models import PipelineChatHistoryModes

        messages = []
        include_summaries = marker == PipelineChatHistoryModes.SUMMARIZE
        for message in self.message_iterator(include_summaries):
            messages.append(message.to_langchain_dict())
            if message.compression_marker and (not marker or marker == message.compression_marker):
                break

        return messages_from_dict(list(reversed(messages)))

    def message_iterator(self, with_summaries=True):
        for message in self.messages.order_by("-created_at").iterator(100):
            yield message
            if with_summaries and message.summary:
                yield message.get_summary_message()

    def attach_files(self, attachment_type: str, files: list[File]):
        resource, _created = self.attachments.get_or_create(tool_type=attachment_type)
        resource.files.add(*files)


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

    @staticmethod
    def from_role(role: str):
        return {
            "user": ChatMessageType.HUMAN,
            "assistant": ChatMessageType.AI,
            "system": ChatMessageType.SYSTEM,
        }[role]

    @property
    def role(self):
        return {
            ChatMessageType.HUMAN: "user",
            ChatMessageType.AI: "assistant",
            ChatMessageType.SYSTEM: "system",
        }[self]


class ChatMessage(BaseModel, TaggedModelMixin, UserCommentsMixin):
    """
    A message in a chat. Analogous to the BaseMessage class in langchain.
    """

    # Metadata keys that should be excluded from the API response
    INTERNAL_METADATA_KEYS = {
        # ID of the thread run
        "openai_run_id",
        "openai_file_ids",
        # boolean indicating that this message has been synced to the thread
        "openai_thread_checkpoint",
    }

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="messages")
    message_type = models.CharField(max_length=10, choices=ChatMessageType.choices)
    content = models.TextField()
    summary = models.TextField(  # noqa DJ001
        null=True, blank=True, help_text="The summary of the conversation up to this point (not including this message)"
    )
    translations = models.JSONField(default=dict, help_text="Dictionary of translated text keyed by the language code")
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["chat", "created_at"]),
            models.Index(fields=["chat", "message_type", "created_at"]),
        ]

    @classmethod
    def make_summary_message(cls, message):
        """A 'summary message' is a special message only ever exists in memory. It is
        not saved to the database. It is used to represent the summary of a chat up to a certain point."""
        from apps.pipelines.models import PipelineChatHistoryModes

        return ChatMessage(
            created_at=message.created_at,
            message_type=ChatMessageType.SYSTEM,
            content=message.summary,
            metadata={"compression_marker": PipelineChatHistoryModes.SUMMARIZE},
        )

    @property
    def trace_info(self) -> list[dict]:
        trace_info = self.metadata.get("trace_info")
        if not trace_info:
            return []

        if isinstance(trace_info, dict):
            # migrate legacy format
            trace_info["trace_provider"] = self.metadata.get("trace_provider")
            return [trace_info]
        return trace_info

    @property
    def is_ai_message(self):
        return self.message_type == ChatMessageType.AI

    @property
    def is_human_message(self):
        return self.message_type == ChatMessageType.HUMAN

    @property
    def is_summary(self):
        from apps.pipelines.models import PipelineChatHistoryModes

        return self.metadata.get("compression_marker") == PipelineChatHistoryModes.SUMMARIZE

    @property
    def compression_marker(self):
        return self.metadata.get("compression_marker")

    @property
    def created_at_datetime(self):
        return quote(self.created_at.isoformat())

    @property
    def role(self):
        return ChatMessageType(self.message_type).role

    def save(self, *args, **kwargs):
        if self.is_summary:
            raise ValueError("Cannot save a summary message")
        super().save(*args, **kwargs)

    def get_summary_message(self):
        if not self.summary:
            raise ValueError("Message does not have a summary")
        return ChatMessage.make_summary_message(self)

    def to_langchain_dict(self) -> dict:
        return self._get_langchain_dict(self.content, self.message_type)

    def to_langchain_message(self) -> BaseMessage:
        return messages_from_dict([self.to_langchain_dict()])[0]

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
        """Returns all files that are attached to this message. This is read from the message metadata.

        Message metadata example:
        {
            "openai_file_ids": ["file_id_1", "file_id_2", ...],
            "ocs_attachment_file_ids": [1,2,3, ...],
        }
        """
        if not self.chat_id:
            # Summary messages are not saved to the DB, so they don't have a chat_id
            return []

        external_ids = []
        ids = []

        metadata_key = ["openai_file_ids", "ocs_attachment_file_ids", "cited_files", "generated_files"]
        for source in metadata_key:
            # openai_file_ids is a list of external ids
            id_list = external_ids if source == "openai_file_ids" else ids
            if file_ids := self.metadata.get(source, []):
                id_list.extend(file_ids)

        return File.objects.filter(Q(id__in=ids) | Q(external_id__in=external_ids), chatattachment__chat=self.chat)

    def get_metadata(self, key: str):
        return self.metadata.get(key, None)

    def add_version_tag(self, version_number: int, is_a_version: bool):
        tag = f"v{version_number}"
        if not is_a_version:
            tag = f"{tag}-unreleased"
        self.create_and_add_tag(tag, self.chat.team, TagCategories.EXPERIMENT_VERSION)

    def add_rating(self, tag: str):
        tag, _ = Tag.objects.get_or_create(
            name=tag,
            team=self.chat.team,
            is_system_tag=False,
            category=TagCategories.RESPONSE_RATING,
        )
        self.add_tag(tag, team=self.chat.team, added_by=None)

    def rating(self) -> str | None:
        if rating := self.tags.filter(category=TagCategories.RESPONSE_RATING).values_list("name", flat=True).first():
            return rating

    def get_processor_bot_tag_name(self) -> str | None:
        """Returns the tag of the bot that generated this message"""
        if self.message_type != ChatMessageType.AI:
            return
        if tag := self.tags.filter(category=TagCategories.BOT_RESPONSE).first():
            return tag.name

    def get_safety_layer_tag_name(self) -> str | None:
        """Returns the name of the safety layer tag, if there is one"""
        if tag := self.tags.filter(category=TagCategories.SAFETY_LAYER_RESPONSE).first():
            return tag.name


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
