from rest_framework import serializers

from apps.channels.models import ChannelPlatform
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
    cursor = serializers.CharField(
        allow_null=True,
        help_text="Pass on the next pull, also when `has_more` is false. Null only when nothing has ever matched.",
    )
    has_more = serializers.BooleanField(help_text="More rows are ready now; pull again with `cursor`.")
    results = SessionSerializer(many=True)
