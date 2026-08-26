"""Request and response serializers for the chatbot write endpoints (#4139).

The write surface mirrors what the UI's own forms accept -- ``ChatbotForm`` for create and
``ChatbotSettingsForm`` for update -- rather than the shape of ``GET /chatbots/{id}/inspect/``.
Inspecting and editing are different jobs, so inspect returns plenty that is not writable
(provider names, voice languages, resolved node parameters); anything not listed here is refused.

Each field carries its form field's name, with references narrowed to ids under the same
``<resource>_id`` convention the discovery endpoints use, so an agent lifts ids straight out of
discovery. The body is flat, so the key an agent writes is the key it reads back.
"""

from typing import Any

from django.db import transaction
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from apps.api.v2.write.base import RejectsUnknownKeys
from apps.api.v2.write.fields import NfcCharField, OptionalTextField, TeamScopedRelatedField
from apps.experiments.helpers import excluded_voice_services
from apps.experiments.models import ConsentForm, Experiment, SyntheticVoice
from apps.pipelines.models import Pipeline
from apps.service_providers.models import TraceProvider, VoiceProvider
from apps.service_providers.utils import get_first_llm_provider_by_team, get_first_llm_provider_model


class ChatbotCreateSerializer(RejectsUnknownKeys, serializers.Serializer):
    """The create request: a working draft and its seeded pipeline, nothing published."""

    name = NfcCharField(max_length=128)
    description = OptionalTextField(default="")

    @transaction.atomic()
    def create(self, validated_data: dict[str, Any]) -> Experiment:
        # Reimplements the ~10 lines of ChatbotForm.save that cannot be reused because they take a
        # request. Unlike the UI's CreateChatbot this deliberately does not publish a version.
        request = self.context["request"]
        team = request.team
        llm_provider = get_first_llm_provider_by_team(team.id)
        llm_provider_model = get_first_llm_provider_model(llm_provider, team.id)
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


@extend_schema_serializer(
    description=(
        "Editable chatbot configuration. Omitted keys are left unchanged. References are given as "
        "ids, which can be read from the discovery endpoints."
    )
)
class ChatbotWriteSerializer(RejectsUnknownKeys, serializers.ModelSerializer):
    """The PATCH request body.

    The writable set is exactly what ``ChatbotSettingsForm`` edits in the UI -- one API field per
    form field, with references narrowed to ids. It is also ``Experiment.VERSIONED_CONTENT_FIELDS``
    minus ``pipeline``, which has its own façade. Both are pinned by
    ``apps/api/v2/write/tests/test_writable_fields.py``. Inspect returns more than this, because
    inspecting and editing are different jobs.

    The public description is set on the decorator above rather than taken from this docstring,
    which drf-spectacular would otherwise publish verbatim into the OpenAPI document.
    """

    name = NfcCharField(max_length=128)
    # Labelled from the model because declaring the field explicitly drops the label ModelSerializer
    # would have derived, and that label is the field's `title` in the published OpenAPI schema.
    description = OptionalTextField(label=Experiment._meta.get_field("description").verbose_name)
    consent_form_id = TeamScopedRelatedField(
        source="consent_form",
        scoped_queryset=lambda request: ConsentForm.objects.working_versions_queryset().filter(team=request.team),
        required=False,
        allow_null=True,
    )
    trace_provider_id = TeamScopedRelatedField(
        source="trace_provider",
        scoped_queryset=lambda request: TraceProvider.objects.filter(team=request.team),
        required=False,
        allow_null=True,
    )
    # Both voice fields apply the same feature-flag exclusion ChatbotSettingsForm applies, so the API
    # cannot wire a voice the settings page would refuse to re-save.
    voice_provider_id = TeamScopedRelatedField(
        source="voice_provider",
        scoped_queryset=lambda request: VoiceProvider.objects.filter(team=request.team).exclude(
            syntheticvoice__service__in=excluded_voice_services(request)
        ),
        required=False,
        allow_null=True,
    )
    synthetic_voice_id = TeamScopedRelatedField(
        source="synthetic_voice",
        # The team's own voices plus the general ones -- the same set /pipeline/options/ draws on.
        scoped_queryset=lambda request: SyntheticVoice.get_for_team(request.team, excluded_voice_services(request)),
        required=False,
        allow_null=True,
    )

    # The two voice columns are written independently but are only meaningful together, so they are
    # checked as a pair against whatever the row already holds.
    VOICE_FIELDS = (("voice_provider_id", "voice_provider"), ("synthetic_voice_id", "synthetic_voice"))

    class Meta:
        model = Experiment
        fields = [
            "name",
            "description",
            "voice_provider_id",
            "synthetic_voice_id",
            "voice_response_behaviour",
            "echo_transcript",
            "trace_provider_id",
            "debug_mode_enabled",
            "conversational_consent_enabled",
            "consent_form_id",
            "seed_message",
            "file_uploads_enabled",
        ]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        supplied = [name for name, source in self.VOICE_FIELDS if source in attrs]
        if not supplied:
            # Neither half was sent. The stored pair is deliberately not re-checked here: a row
            # that predates this endpoint may hold a half-set voice, and validating it would fail
            # a PATCH that has nothing to do with the voice.
            return attrs

        # The pair as it would be *after* this request: the incoming value where one was sent, the
        # stored one otherwise. An explicit null arrives as a present key holding None, so `get`
        # with a default keeps "sent as null" (clear it) distinct from "not sent" (keep it).
        # `getattr` rather than attribute access: only PATCH builds this serializer today, but an
        # instance-less use would otherwise be an AttributeError -- a 500 -- rather than a check
        # against an empty pair.
        provider = attrs.get("voice_provider", getattr(self.instance, "voice_provider", None))
        voice = attrs.get("synthetic_voice", getattr(self.instance, "synthetic_voice", None))

        error = self._voice_pair_error(provider, voice)
        if error:
            raise serializers.ValidationError(dict.fromkeys(supplied, error))
        return attrs

    @staticmethod
    def _voice_pair_error(provider: VoiceProvider | None, voice: SyntheticVoice | None) -> str | None:
        """Why this (provider, voice) pair cannot be spoken, or None if it can.

        Both-null is a valid pair: it means the chatbot has no voice.
        """
        if provider is None and voice is None:
            return None
        if provider is None or voice is None:
            present = "synthetic_voice_id" if provider is None else "voice_provider_id"
            missing = "voice_provider_id" if provider is None else "synthetic_voice_id"
            return (
                "voice_provider_id and synthetic_voice_id must be set together or both be null. "
                f"This change would leave {present} set with no {missing}."
            )
        # SyntheticVoice.service is "AWS"; VoiceProvider.type is "aws".
        if voice.service.lower() != provider.type.lower():
            return (
                f"Voice '{voice.name}' is a {voice.service} voice and cannot be spoken by "
                f"the {provider.type} provider '{provider.name}'."
            )
        if voice.voice_provider_id not in (None, provider.id):
            return f"Voice '{voice.name}' belongs to a different voice provider."
        return None


class ChatbotDetailSerializer(serializers.ModelSerializer):
    """The create and PATCH response: the same shape the request accepts, plus read-only identity."""

    id = serializers.UUIDField(source="public_id", read_only=True)
    # Declared explicitly rather than left to ModelSerializer: `version_number` is a real model
    # field and would otherwise be generated as writable in the OpenAPI schema.
    pipeline_id = serializers.IntegerField(read_only=True, allow_null=True)
    version_number = serializers.IntegerField(read_only=True)
    consent_form_id = serializers.IntegerField(read_only=True, allow_null=True)
    trace_provider_id = serializers.IntegerField(read_only=True, allow_null=True)
    voice_provider_id = serializers.IntegerField(read_only=True, allow_null=True)
    synthetic_voice_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Experiment
        fields = [
            "id",
            "pipeline_id",
            "version_number",
            "name",
            "description",
            "voice_provider_id",
            "synthetic_voice_id",
            "voice_response_behaviour",
            "echo_transcript",
            "trace_provider_id",
            "debug_mode_enabled",
            "conversational_consent_enabled",
            "consent_form_id",
            "seed_message",
            "file_uploads_enabled",
        ]
        # Nothing here is writable: this serializer only ever renders a response. These are the
        # fields ModelSerializer generates, and generated clients would otherwise model them as
        # writable on a response-only component.
        read_only_fields = [
            "name",
            "description",
            "voice_response_behaviour",
            "echo_transcript",
            "debug_mode_enabled",
            "conversational_consent_enabled",
            "seed_message",
            "file_uploads_enabled",
        ]
