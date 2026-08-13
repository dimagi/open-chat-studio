"""Request and response serializers for the chatbot write endpoints (#4139).

Key paths mirror ``GET /chatbots/{id}/inspect/``; references are addressed by id using the same
``<resource>_id`` convention the discovery endpoints use, so an agent reads structure from inspect
and lifts ids straight out of discovery.
"""

import unicodedata

from django.db import transaction
from rest_framework import serializers

from apps.api.v2.write.fields import TeamScopedRelatedField
from apps.experiments.models import ConsentForm, Experiment
from apps.pipelines.models import Pipeline
from apps.service_providers.models import TraceProvider
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


class ChatbotSettingsSerializer(serializers.ModelSerializer):
    """The writable half of the inspect ``settings`` block.

    Read-side twin: ``apps.api.v2.inspect.serializers.InspectSettingsSerializer``. Mounted with
    ``source="*"`` on the parent, so DRF merges these straight onto the experiment (``set_value``
    with empty ``source_attrs`` does a ``dict.update``) and an omitted key under a partial PATCH is
    skipped rather than reset -- which is the whole merge semantics, with no custom code.
    """

    class Meta:
        model = Experiment
        fields = [
            "seed_message",
            "conversational_consent_enabled",
            "voice_response_behaviour",
            "echo_transcript",
            "debug_mode_enabled",
            "file_uploads_enabled",
            "participant_allowlist",
        ]


class ChatbotWriteSerializer(serializers.ModelSerializer):
    """The PATCH request body.

    The writable set is exactly ``Experiment.VERSIONED_CONTENT_FIELDS`` minus ``pipeline``, which
    has its own façade -- so what inspect shows you is what you can write.
    """

    settings = ChatbotSettingsSerializer(source="*", required=False)
    consent_form_id = TeamScopedRelatedField(
        source="consent_form",
        get_team_queryset=lambda team: ConsentForm.objects.working_versions_queryset().filter(team=team),
        required=False,
        allow_null=True,
    )
    trace_provider_id = TeamScopedRelatedField(
        source="trace_provider",
        get_team_queryset=lambda team: TraceProvider.objects.filter(team=team),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Experiment
        fields = ["name", "description", "settings", "consent_form_id", "trace_provider_id"]

    def validate_name(self, value):
        return normalize_chatbot_name(value)


class ChatbotDetailSerializer(serializers.ModelSerializer):
    """The PATCH response: the same shape the request accepts, plus read-only identity."""

    id = serializers.UUIDField(source="public_id", read_only=True)
    # Declared explicitly rather than left to ModelSerializer: `version_number` is a real model
    # field and would otherwise be generated as writable in the OpenAPI schema.
    pipeline_id = serializers.IntegerField(read_only=True, allow_null=True)
    version_number = serializers.IntegerField(read_only=True)
    settings = ChatbotSettingsSerializer(source="*", read_only=True)
    consent_form_id = serializers.IntegerField(read_only=True, allow_null=True)
    trace_provider_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Experiment
        fields = [
            "id",
            "pipeline_id",
            "version_number",
            "name",
            "description",
            "settings",
            "consent_form_id",
            "trace_provider_id",
        ]
