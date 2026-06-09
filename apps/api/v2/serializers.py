from rest_framework import serializers

from apps.experiments.models import Experiment


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
