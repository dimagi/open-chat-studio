from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.experiments.models import Experiment
from apps.teams.models import Team
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

    url = serializers.HyperlinkedIdentityField(
        view_name="api:v2:chatbot-detail", lookup_field="public_id", lookup_url_kwarg="id", label="API URL"
    )
    id = serializers.UUIDField(source="public_id")
    versions = ChatbotVersionSerializer(many=True)

    class Meta:
        model = Experiment
        fields = ["id", "name", "url", "version_number", "versions"]


class MeTeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["id", "name", "slug"]


class MeSerializer(serializers.ModelSerializer):
    team = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = ["id", "username", "email", "first_name", "last_name", "team"]

    @extend_schema_field(MeTeamSerializer)
    def get_team(self, obj):
        team = self.context.get("team")
        return MeTeamSerializer(team).data if team else None
