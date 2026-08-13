"""Request and response serializers for the chatbot write endpoints (#4139).

Key paths mirror ``GET /chatbots/{id}/inspect/``; references are named the way
``GET /pipeline/options/`` names them, so an agent reads structure from inspect and lifts ids
straight out of discovery.
"""

import unicodedata

from django.db import transaction
from rest_framework import serializers

from apps.experiments.models import Experiment
from apps.pipelines.models import Pipeline
from apps.service_providers.utils import get_first_llm_provider_by_team, get_first_llm_provider_model


def normalize_chatbot_name(value: str) -> str:
    """Mirrors ``CreateChatbot.form_valid`` so API- and UI-created names compare equal."""
    return unicodedata.normalize("NFC", value)


class ChatbotCreateSerializer(serializers.Serializer):
    """The create request: a working draft and its seeded pipeline, nothing published."""

    name = serializers.CharField(max_length=128)
    description = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_name(self, value):
        return normalize_chatbot_name(value)

    @transaction.atomic()
    def create(self, validated_data):
        # Reimplements the ~10 lines of ChatbotForm.save that cannot be reused because they take a
        # request. Unlike the UI's CreateChatbot this deliberately does not publish a version.
        request = self.context["request"]
        team = request.team
        llm_provider = get_first_llm_provider_by_team(team.id)
        llm_provider_model = get_first_llm_provider_model(llm_provider, team.id) if llm_provider else None
        pipeline = Pipeline.create_default_pipeline_with_name(
            team,
            validated_data["name"],
            llm_provider.id if llm_provider else None,
            llm_provider_model,
        )
        return Experiment.objects.create(
            team=team,
            # A client-credentials (machine) token has no user behind it.
            owner=request.user if request.user.is_authenticated else None,
            name=validated_data["name"],
            description=validated_data["description"],
            pipeline=pipeline,
        )


class ChatbotCreatedSerializer(serializers.Serializer):
    """The create response: spec 5.1's three keys and nothing more."""

    id = serializers.UUIDField(source="public_id", read_only=True)
    pipeline_id = serializers.IntegerField(read_only=True)
    version_number = serializers.IntegerField(read_only=True)
