"""Request and response serializers for the chatbot write endpoints (#4139).

Key paths mirror ``GET /chatbots/{id}/inspect/``; references are addressed by id using the same
``<resource>_id`` convention the discovery endpoints use, so an agent reads structure from inspect
and lifts ids straight out of discovery.
"""

import unicodedata

from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.api.v2.write.fields import TeamScopedRelatedField
from apps.experiments.models import ConsentForm, Experiment, SyntheticVoice
from apps.pipelines.models import Pipeline
from apps.service_providers.models import TraceProvider, VoiceProvider
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


# Sentinel distinguishing "voice was omitted" from an explicit "voice": null.
_MISSING = object()


class VoicePairSerializer(serializers.Serializer):
    """A (voice provider, synthetic voice) pair.

    The two columns are only meaningful together, so both keys are required whenever ``voice`` is
    present at all; ``"voice": null`` on the parent clears both.
    """

    # `pk_field` is what lets drf-spectacular type these as integers: this is a plain Serializer
    # with no Meta.model, so it cannot infer the pk type from a relation the way it can on
    # ChatbotWriteSerializer's reference fields.
    voice_provider_id = TeamScopedRelatedField(
        source="voice_provider",
        get_team_queryset=lambda team: VoiceProvider.objects.filter(team=team),
        pk_field=serializers.IntegerField(),
    )
    synthetic_voice_id = TeamScopedRelatedField(
        source="synthetic_voice",
        # The team's own voices plus the general ones -- the same set /pipeline/options/ draws on.
        get_team_queryset=lambda team: SyntheticVoice.get_for_team(team, []),
        pk_field=serializers.IntegerField(),
    )

    def validate(self, attrs):
        # A partial PATCH on the parent propagates into this serializer (Field.validate_empty_values
        # consults self.root.partial), so a missing key would be skipped rather than required.
        missing = {
            name: "This field is required."
            for name, source in (("voice_provider_id", "voice_provider"), ("synthetic_voice_id", "synthetic_voice"))
            if source not in attrs
        }
        if missing:
            raise serializers.ValidationError(missing)

        provider, voice = attrs["voice_provider"], attrs["synthetic_voice"]
        # SyntheticVoice.service is "AWS"; VoiceProvider.type is "aws".
        if voice.service.lower() != provider.type.lower():
            raise serializers.ValidationError(
                f"Voice '{voice.name}' is a {voice.service} voice and cannot be spoken by "
                f"the {provider.type} provider '{provider.name}'."
            )
        if voice.voice_provider_id not in (None, provider.id):
            raise serializers.ValidationError(f"Voice '{voice.name}' belongs to a different voice provider.")
        return attrs


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
    voice = VoicePairSerializer(required=False, allow_null=True)

    class Meta:
        model = Experiment
        fields = ["name", "description", "settings", "consent_form_id", "trace_provider_id", "voice"]

    def validate_name(self, value):
        return normalize_chatbot_name(value)

    def update(self, instance, validated_data):
        # `voice` has no source, so ModelSerializer.update would set a junk attribute; apply the
        # pair explicitly. The sentinel separates "omitted" (leave alone) from null (clear both).
        voice = validated_data.pop("voice", _MISSING)
        if voice is not _MISSING:
            instance.voice_provider = voice["voice_provider"] if voice else None
            instance.synthetic_voice = voice["synthetic_voice"] if voice else None
        return super().update(instance, validated_data)


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
    voice = serializers.SerializerMethodField()

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
            "voice",
        ]

    @extend_schema_field(VoicePairSerializer(allow_null=True))
    def get_voice(self, experiment) -> dict | None:
        # Experiment has no `voice` attribute; build the pair, mirroring how the inspect
        # serializer's get_voice emits null when either half is unset.
        if not (experiment.voice_provider_id and experiment.synthetic_voice_id):
            return None
        return {
            "voice_provider_id": experiment.voice_provider_id,
            "synthetic_voice_id": experiment.synthetic_voice_id,
        }
