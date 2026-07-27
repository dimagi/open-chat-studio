from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.api.serializers import ApiUrlField, TeamSerializer
from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment, ExperimentSession
from apps.users.helpers import user_has_confirmed_email_address
from apps.users.models import CustomUser


class ChatbotVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Experiment
        fields = ["name", "version_number", "is_default_version", "version_description"]


class ChatbotSerializer(serializers.ModelSerializer):
    """Basic chatbot fields for the list and retrieve endpoints.

    The full configuration is served by the ``inspect`` action instead (ADR-0024). v2 calls these
    "chatbots" rather than "experiments" (ADR-0023).
    """

    url = ApiUrlField(
        openapi_example="https://example.com/api/v2/chatbots/123e4567-e89b-12d3-a456-426614174000/",
        view_name="api:v2:chatbot-detail",
        lookup_field="public_id",
        lookup_url_kwarg="id",
        label="API URL",
    )
    id = serializers.UUIDField(source="public_id")
    versions = ChatbotVersionSerializer(many=True)

    class Meta:
        model = Experiment
        fields = ["id", "name", "url", "version_number", "versions"]


class MeSerializer(serializers.ModelSerializer):
    team = serializers.SerializerMethodField()
    email_verified = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = ["id", "username", "email", "first_name", "last_name", "email_verified", "team"]

    @extend_schema_field(TeamSerializer)
    def get_team(self, obj):
        team = self.context.get("team")
        return TeamSerializer(team).data if team else None

    @extend_schema_field(serializers.BooleanField())
    def get_email_verified(self, obj):
        return user_has_confirmed_email_address(obj, obj.email)


class TriggerBotChannelSerializer(serializers.Serializer):
    """The channel a trigger_bot session runs on, plus any platform-specific data.

    Unlike v1 (where ``channel`` is just the platform slug), v2 nests the platform under a dict so
    platform-specific fields can live alongside it in ``data`` without widening the top-level shape.
    """

    platform = serializers.CharField(help_text="Channel platform slug, e.g. 'commcare_connect'.")
    data = serializers.DictField(
        help_text=(
            "Platform-specific channel data. For CommCare Connect this includes "
            "'external_channel_id'; empty for platforms without extra channel data."
        )
    )


class TriggerBotMessageResponse(serializers.ModelSerializer):
    session_id = serializers.ReadOnlyField(source="external_id")
    url = ApiUrlField(
        openapi_example="https://example.com/api/sessions/123e4567-e89b-12d3-a456-426614174000/",
        view_name="api:session-detail",
        lookup_field="external_id",
        lookup_url_kwarg="id",
    )
    team = TeamSerializer(read_only=True)
    channel = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentSession
        fields = ["session_id", "url", "team", "channel"]

    @extend_schema_field(TriggerBotChannelSerializer)
    def get_channel(self, obj) -> dict:
        data = {}
        if obj.platform == ChannelPlatform.COMMCARE_CONNECT:
            participant_data = self.context.get("participant_data")
            if participant_data is not None:
                external_channel_id = participant_data.system_metadata.get("commcare_connect_channel_id")
                if external_channel_id:
                    data["external_channel_id"] = external_channel_id
        return {"platform": obj.platform, "data": data}
