from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.api.serializers import FileSerializer
from apps.channels.models import ChannelPlatform
from apps.chat.models import ChatMessage, ChatMessageMetadataKeys, ChatMessageType
from apps.experiments.models import ExperimentSession

MAX_LIMIT = 1000


class SyncQuerySerializer(serializers.Serializer):
    cursor = serializers.CharField(
        required=False, help_text="The `cursor` from the previous response. Omit it on the first pull."
    )
    limit = serializers.IntegerField(required=False, default=100, min_value=1, max_value=MAX_LIMIT)
    chatbot = serializers.UUIDField(required=False, help_text="A chatbot's public ID.")


class SessionQuerySerializer(SyncQuerySerializer):
    participant = serializers.UUIDField(required=False, help_text="A participant's public ID.")
    platform = serializers.ChoiceField(required=False, choices=ChannelPlatform.choices)
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Sessions with any of these tags. Repeat the parameter.",
    )


class SessionSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="external_id", read_only=True)
    chatbot_id = serializers.UUIDField(source="experiment.public_id", read_only=True)
    participant_id = serializers.UUIDField(source="participant.public_id", read_only=True)
    tags = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentSession
        fields = [
            "id",
            "chatbot_id",
            "participant_id",
            "platform",
            "status",
            "state",
            "tags",
            "created_at",
            "updated_at",
            "ended_at",
        ]
        read_only_fields = fields

    def get_tags(self, session) -> list[str]:
        """Read from the tagged items the view attaches to each page."""
        return sorted(item.tag.name for item in session.chat.prefetched_tagged_items)


class SessionPageSerializer(serializers.Serializer):
    cursor_help = "Pass on the next pull, also when `has_more` is false. Null only when nothing has ever matched."
    has_more_help = "More rows are ready now; pull again with `cursor`."

    cursor = serializers.CharField(allow_null=True, help_text=cursor_help)
    has_more = serializers.BooleanField(help_text=has_more_help)
    results = SessionSerializer(many=True)


class MessageQuerySerializer(SyncQuerySerializer):
    session = serializers.CharField(required=False, help_text="A session's ID.")
    participant = serializers.UUIDField(required=False, help_text="A participant's public ID.")


class MessageSerializer(serializers.ModelSerializer):
    session_id = serializers.CharField(source="chat.experiment_session.external_id", read_only=True)
    role = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    attachments = FileSerializer(source="get_attached_files", many=True, read_only=True)

    class Meta:
        model = ChatMessage
        fields = ["id", "session_id", "role", "content", "metadata", "tags", "attachments", "created_at", "updated_at"]
        read_only_fields = fields

    def get_role(self, message) -> str:
        return ChatMessageType(message.message_type).role

    def get_metadata(self, message) -> dict:
        internal = ChatMessageMetadataKeys.internal_keys()
        return {key: value for key, value in message.metadata.items() if key not in internal}

    def get_tags(self, message) -> list[str]:
        return sorted(tag.name for tag in message.tags.all())


class MessagePageSerializer(serializers.Serializer):
    cursor = serializers.CharField(allow_null=True, help_text=SessionPageSerializer.cursor_help)
    has_more = serializers.BooleanField(help_text=SessionPageSerializer.has_more_help)
    results = MessageSerializer(many=True)


class SessionDetailSerializer(SessionSerializer):
    messages = serializers.SerializerMethodField()

    class Meta(SessionSerializer.Meta):
        fields = [*SessionSerializer.Meta.fields, "messages"]
        read_only_fields = fields

    @extend_schema_field(MessageSerializer(many=True))
    def get_messages(self, session) -> list:
        """Read from the messages the view prefetches."""
        return MessageSerializer(session.chat.synced_messages, many=True, context=self.context).data
