"""Request and response serializers for the chatbot write endpoints (#4139).

The write surface mirrors what the UI's own forms accept -- ``ChatbotForm`` for create and
``ChatbotSettingsForm`` for update -- rather than the shape of ``GET /chatbots/{id}/inspect/``.
Inspecting and editing are different jobs, so inspect returns plenty that is not writable
(provider names, voice languages, resolved node parameters); anything not listed here is refused.

Each field carries its form field's name, with references narrowed to ids under the same
``<resource>_id`` convention the discovery endpoints use, so an agent lifts ids straight out of
discovery. ``settings`` is the one nested block, because it is dict-shaped in the API already.
"""

import unicodedata

from django.db import transaction
from rest_framework import serializers

from apps.api.v2.write.fields import TeamScopedRelatedField
from apps.experiments.models import ConsentForm, Experiment, SyntheticVoice
from apps.pipelines.models import Pipeline
from apps.service_providers.models import TraceProvider, VoiceProvider
from apps.service_providers.utils import get_first_llm_provider_by_team, get_first_llm_provider_model


def normalize_chatbot_name(value: str) -> str:
    """Mirrors ``CreateChatbot.form_valid`` so API- and UI-created names compare equal."""
    return unicodedata.normalize("NFC", value)


class RejectsUnknownKeys:
    """Refuse a request body carrying keys this serializer does not declare.

    DRF's default is to drop them silently, which for a human is a typo they spot in the echoed
    response and for an agent is a 200 that wrote nothing. The consumer here is an agent, so a
    misspelled key has to be an error it can act on.

    Hooked into ``to_internal_value`` rather than ``validate`` because that is the only place a
    *nested* serializer sees its own raw input: ``initial_data`` is set on the root serializer
    alone, so a ``validate``-based check could not reach ``settings`` or ``voice``.
    """

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                accepted = ", ".join(sorted(self.fields))
                raise serializers.ValidationError(
                    {key: f"Unrecognised field. Accepted here: {accepted}." for key in unknown}
                )
        return super().to_internal_value(data)


class ChatbotCreateSerializer(RejectsUnknownKeys, serializers.Serializer):
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


class ChatbotSettingsSerializer(RejectsUnknownKeys, serializers.ModelSerializer):
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


class ChatbotWriteSerializer(RejectsUnknownKeys, serializers.ModelSerializer):
    """The PATCH request body.

    The writable set is exactly what ``ChatbotSettingsForm`` edits in the UI -- one API field per
    form field, with references narrowed to ids. It is also ``Experiment.VERSIONED_CONTENT_FIELDS``
    minus ``pipeline``, which has its own façade; ``apps/api/v2/write/tests/test_writable_fields.py``
    pins both. Inspect returns more than this, because inspecting and editing are different jobs.
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
    voice_provider_id = TeamScopedRelatedField(
        source="voice_provider",
        get_team_queryset=lambda team: VoiceProvider.objects.filter(team=team),
        required=False,
        allow_null=True,
    )
    synthetic_voice_id = TeamScopedRelatedField(
        source="synthetic_voice",
        # The team's own voices plus the general ones -- the same set /pipeline/options/ draws on.
        get_team_queryset=lambda team: SyntheticVoice.get_for_team(team, []),
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
            "settings",
            "consent_form_id",
            "trace_provider_id",
            "voice_provider_id",
            "synthetic_voice_id",
        ]

    def validate_name(self, value):
        return normalize_chatbot_name(value)

    def validate(self, attrs):
        supplied = [name for name, source in self.VOICE_FIELDS if source in attrs]
        if not supplied:
            # Neither half was sent. The stored pair is deliberately not re-checked here: a row
            # that predates this endpoint may hold a half-set voice, and validating it would fail
            # a PATCH that has nothing to do with the voice.
            return attrs

        # The pair as it would be *after* this request: the incoming value where one was sent, the
        # stored one otherwise. An explicit null arrives as a present key holding None, so `get`
        # with a default keeps "sent as null" (clear it) distinct from "not sent" (keep it).
        provider = attrs.get("voice_provider", self.instance.voice_provider)
        voice = attrs.get("synthetic_voice", self.instance.synthetic_voice)

        error = self._voice_pair_error(provider, voice)
        if error:
            raise serializers.ValidationError(dict.fromkeys(supplied, error))
        return attrs

    @staticmethod
    def _voice_pair_error(provider, voice) -> str | None:
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
    """The PATCH response: the same shape the request accepts, plus read-only identity."""

    id = serializers.UUIDField(source="public_id", read_only=True)
    # Declared explicitly rather than left to ModelSerializer: `version_number` is a real model
    # field and would otherwise be generated as writable in the OpenAPI schema.
    pipeline_id = serializers.IntegerField(read_only=True, allow_null=True)
    version_number = serializers.IntegerField(read_only=True)
    settings = ChatbotSettingsSerializer(source="*", read_only=True)
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
            "settings",
            "consent_form_id",
            "trace_provider_id",
            "voice_provider_id",
            "synthetic_voice_id",
        ]
