from rest_framework import serializers

from apps.experiments.models import Experiment


class ChatbotVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Experiment
        fields = ["name", "version_number", "is_default_version", "version_description"]


class ChatbotSerializer(serializers.ModelSerializer):
    """Minimal v2 representation of a Chatbot (list/retrieve).

    The rich, denormalized configuration lives at the ``inspect`` action, not here
    (ADR-0024). v2 renames the external surface from ``experiment`` to ``chatbot`` (ADR-0023).
    """

    url = serializers.HyperlinkedIdentityField(
        view_name="api:v2:chatbot-detail", lookup_field="public_id", lookup_url_kwarg="id", label="API URL"
    )
    id = serializers.UUIDField(source="public_id")
    versions = ChatbotVersionSerializer(many=True)

    class Meta:
        model = Experiment
        fields = ["id", "name", "url", "version_number", "versions"]
