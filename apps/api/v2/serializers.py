from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.api.serializers import ApiUrlField, TeamSerializer
from apps.experiments.models import Experiment
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
