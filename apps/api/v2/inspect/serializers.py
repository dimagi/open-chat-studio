"""Secrets-excluding serializers for the chatbot inspect projection.

Every resource is serialized through an explicit allowlist of fields — never ``__all__`` and
never a denylist (ADR-0027). Adding a field to a model never exposes it here by default.

Encrypted provider ``config`` blobs, signed file-storage URLs, and channel ``extra_data`` are
excluded outright. Provider + model pairs are flattened into a single concept object
(``llm`` / ``voice`` / ``embedding``) by the ``Flattened*`` serializers (ADR-0025); they render
the resolved-instance pairs the collector batch-loaded once, so every reference site inlines a
copy of the same loaded objects.
"""

import dataclasses
from functools import cached_property
from typing import TYPE_CHECKING

from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from apps.assistants.models import OpenAiAssistant
from apps.channels.models import ExperimentChannel
from apps.custom_actions.models import CustomAction
from apps.documents.models import Collection
from apps.experiments.models import ConsentForm, SourceMaterial, Survey
from apps.files.models import File

if TYPE_CHECKING:
    from apps.custom_actions.schema_utils import APIOperationDetails


@extend_schema_serializer(component_name="InspectFile")
class FileSerializer(serializers.ModelSerializer):
    """Identity-lean file view. Excludes the signed ``file`` storage URL, ``summary`` and
    ``metadata`` (the latter two are dropped for size, not secrecy — design D8/Q6)."""

    class Meta:
        model = File
        fields = ["id", "name", "content_type", "content_size", "external_source", "external_id", "purpose"]


class SourceMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceMaterial
        fields = ["id", "topic", "description", "material"]


class ConsentFormSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentForm
        fields = ["id", "name", "consent_text", "capture_identifier", "identifier_label", "identifier_type"]


class SurveySerializer(serializers.ModelSerializer):
    class Meta:
        model = Survey
        fields = ["id", "name", "url", "confirmation_text"]


class AssistantSerializer(serializers.ModelSerializer):
    """``instructions`` and ``assistant_id`` are intentionally exposed (resolved Q4/Q5)."""

    class Meta:
        model = OpenAiAssistant
        fields = ["id", "name", "assistant_id", "instructions", "builtin_tools", "tools", "temperature", "top_p"]


class ProviderSerializer(serializers.Serializer):
    """Minimal provider reference — A plain ``Serializer`` (not ``ModelSerializer``) so it works for
    any provider model (Llm/Voice/Messaging/Auth/Trace) via duck typing. Serializing ``None``
    yields ``None`` when nested; standalone call sites must guard for ``None`` themselves."""

    id = serializers.IntegerField()
    type = serializers.CharField()
    name = serializers.CharField()


class ChannelSerializer(serializers.ModelSerializer):
    """Allowlist ``platform`` + ``name`` + embedded ``messaging_provider`` ref. ``extra_data``
    (freeform auth material) is excluded wholesale (resolved Q8)."""

    messaging_provider = ProviderSerializer(allow_null=True)

    class Meta:
        model = ExperimentChannel
        fields = ["platform", "name", "messaging_provider"]


# ── Resolved-instance value objects ──────────────────────────────────────────────────────────────
# Produced by the collector/builder, consumed as serializer instances. They carry loaded model
# objects only; all rendering happens in the serializer classes below.
@dataclasses.dataclass
class ProviderModelPair:
    """Resolved (provider, model) pair behind a flattened ``llm`` / ``embedding`` object."""

    provider: object | None
    model: object | None

    @classmethod
    def from_parts(cls, provider, model) -> "ProviderModelPair | None":
        """``None`` when both halves are absent, so the pair renders as JSON ``null`` rather than
        an all-null object. The single home of the absent-pair rule for llm/embedding objects."""
        if provider is None and model is None:
            return None
        return cls(provider, model)

    @property
    def type(self):
        """Provider type, falling back to the model's type when the provider half is unset."""
        if self.provider is not None:
            return self.provider.type
        return self.model.type if self.model is not None else None


@dataclasses.dataclass
class VoicePair:
    """Resolved (voice provider, synthetic voice) pair behind a flattened ``voice`` object."""

    provider: object | None
    voice: object | None

    @classmethod
    def from_parts(cls, provider, voice) -> "VoicePair | None":
        """``None`` when both halves are absent, so the pair renders as JSON ``null`` rather than
        an all-null object. The single home of the absent-pair rule for voice objects."""
        if provider is None and voice is None:
            return None
        return cls(provider, voice)


@dataclasses.dataclass
class CustomActionSelection:
    """A reference site's custom-action selection: the action plus the selected operation ids."""

    action: CustomAction
    operation_ids: list[str]

    @cached_property
    def resolved_operations(self) -> "list[APIOperationDetails]":
        """The selected operations still present in the action's schema; a selected operation no
        longer in the schema resolves to absent."""
        operations_by_id = self.action.get_operations_by_id()
        return [op for op in (operations_by_id.get(oid) for oid in self.operation_ids) if op]


# ── Flattened / selection serializers (ADR-0025) ─────────────────────────────────────────────────
class FlattenedProviderSerializer(serializers.Serializer):
    """Provider half shared by the flattened pair serializers — renders ``pair.provider`` with
    null fields when the half is absent (ADR-0025)."""

    provider_id = serializers.IntegerField(source="provider.id", default=None, allow_null=True)
    provider_name = serializers.CharField(source="provider.name", default=None, allow_null=True)


class FlattenedEmbeddingSerializer(FlattenedProviderSerializer):
    """A collection's embedding ``(LlmProvider, EmbeddingProviderModel)`` pair flattened into one
    object. Instance: :class:`ProviderModelPair`. Missing halves render their fields as null."""

    # ``ProviderModelPair.type`` — provider type with model-type fallback.
    type = serializers.CharField(allow_null=True)
    model = serializers.CharField(source="model.name", default=None, allow_null=True)


class FlattenedLlmSerializer(FlattenedEmbeddingSerializer):
    """An ``(LlmProvider, LlmProviderModel)`` pair flattened into one ``llm`` object (ADR-0025)."""

    max_token_limit = serializers.IntegerField(source="model.max_token_limit", default=None, allow_null=True)
    deprecated = serializers.BooleanField(source="model.deprecated", default=None, allow_null=True)


class FlattenedVoiceSerializer(FlattenedProviderSerializer):
    """A ``(VoiceProvider, SyntheticVoice)`` pair flattened into one ``voice`` object (ADR-0025).
    Instance: :class:`VoicePair`."""

    # Unlike the llm/embedding pairs, ``type`` has no fallback to the voice half — a provider-less
    # voice renders ``type: null`` (matches the pre-refactor shape).
    type = serializers.CharField(source="provider.type", default=None, allow_null=True)
    voice_name = serializers.CharField(source="voice.name", default=None, allow_null=True)
    language = serializers.CharField(source="voice.language", default=None, allow_null=True)
    neural = serializers.BooleanField(source="voice.neural", default=None, allow_null=True)


class MediaCollectionSerializer(serializers.ModelSerializer):
    """Collection identity + files, without embedding — the media-collection shape (ADR-0025)."""

    files = FileSerializer(many=True)

    class Meta:
        model = Collection
        fields = ["id", "name", "files"]


class IndexedCollectionSerializer(MediaCollectionSerializer):
    """An indexed/RAG collection: identity + flattened embedding pair + files (ADR-0025)."""

    embedding = serializers.SerializerMethodField()

    class Meta(MediaCollectionSerializer.Meta):
        fields = ["id", "name", "embedding", "files"]

    @extend_schema_field(FlattenedEmbeddingSerializer(allow_null=True))
    def get_embedding(self, collection):
        pair = ProviderModelPair.from_parts(collection.llm_provider, collection.embedding_provider_model)
        return FlattenedEmbeddingSerializer(pair).data if pair is not None else None


class ApiSchemaDigestSerializer(serializers.Serializer):
    """A custom action's OpenAPI schema reduced to the selected operations' path digest."""

    paths = serializers.ListField(child=serializers.CharField())


class CustomActionSerializer(serializers.Serializer):
    """Custom action with ``allowed_operations`` reflecting the operations selected at the
    reference site — never the action's full operation set — and its OpenAPI schema reduced to the
    selected operations' path digest (resolved Q7 — size, not secrecy). Auth provider is
    ``{id, type, name}`` only (ADR-0027). Instance: :class:`CustomActionSelection`."""

    id = serializers.IntegerField(source="action.id")
    name = serializers.CharField(source="action.name")
    description = serializers.CharField(source="action.description", allow_blank=True)
    server_url = serializers.CharField(source="action.server_url")
    allowed_operations = serializers.SerializerMethodField()
    api_schema = serializers.SerializerMethodField()
    auth_provider = ProviderSerializer(source="action.auth_provider", allow_null=True)

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_allowed_operations(self, selection) -> list[str]:
        return [operation.operation_id for operation in selection.resolved_operations]

    @extend_schema_field(ApiSchemaDigestSerializer)
    def get_api_schema(self, selection) -> dict:
        # Rendered through the digest serializer so the declared schema and the produced bytes
        # share one definition. Sorted for a deterministic digest (selection order carries no
        # meaning for paths).
        paths = sorted({operation.path for operation in selection.resolved_operations})
        return ApiSchemaDigestSerializer({"paths": paths}).data


# ── Envelope serializers (ADR-0024) ──────────────────────────────────────────────────────────────
# ``ChatbotInspectSerializer`` renders the entire inspect payload from the ``InspectContext``
# assembled by the builder, and doubles as the response schema declared on the ``chatbot_inspect``
# action — the OpenAPI doc is derived from the same classes that render the bytes, so the two
# cannot drift for any declared field. The only free-form leaves are node ``params`` and
# trigger-action ``params`` (their keys vary by node / action type).
#
# Node reference fields are ``required=False`` and non-nullable: which keys appear depends on the
# node type, and a reference that is unset or resolves to absent (cross-team / deleted id) is
# omitted entirely.
class InspectSettingsSerializer(serializers.Serializer):
    """Non-secret Experiment fields surfaced under ``settings``. Sourced straight off the
    experiment instance."""

    seed_message = serializers.CharField(allow_blank=True)
    conversational_consent_enabled = serializers.BooleanField()
    voice_response_behaviour = serializers.CharField()
    echo_transcript = serializers.BooleanField()
    use_processor_bot_voice = serializers.BooleanField()
    debug_mode_enabled = serializers.BooleanField()
    file_uploads_enabled = serializers.BooleanField()
    participant_allowlist = serializers.ListField(child=serializers.CharField())


@extend_schema_serializer(component_name="InspectGraphNode")
class GraphNodeSerializer(serializers.Serializer):
    node_id = serializers.CharField(source="flow_id")
    type = serializers.CharField()
    label = serializers.CharField()


@extend_schema_serializer(component_name="InspectGraphEdge")
class GraphEdgeSerializer(serializers.Serializer):
    source = serializers.CharField()
    target = serializers.CharField()
    source_handle = serializers.CharField(allow_null=True)
    target_handle = serializers.CharField(allow_null=True)


@extend_schema_serializer(component_name="InspectGraph")
class GraphSerializer(serializers.Serializer):
    """Pipeline topology digest: nodes as identity triples, edges with positions stripped."""

    nodes = GraphNodeSerializer(many=True)
    edges = GraphEdgeSerializer(many=True)


class InspectNodeSerializer(serializers.Serializer):
    """One pipeline node with its resource references inlined (ADR-0025)."""

    node_id = serializers.CharField(source="flow_id")
    type = serializers.CharField()
    label = serializers.CharField()
    params = serializers.DictField(help_text="The node's non-resource configuration, verbatim; keys vary by node type.")
    llm = FlattenedLlmSerializer(required=False)
    voice = FlattenedVoiceSerializer(required=False)
    source_material = SourceMaterialSerializer(required=False)
    assistant = AssistantSerializer(required=False)
    custom_actions = CustomActionSerializer(many=True, required=False)
    media_collection = MediaCollectionSerializer(required=False)
    indexed_collections = IndexedCollectionSerializer(many=True, required=False)


class InspectPipelineSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    version_number = serializers.IntegerField()
    graph = GraphSerializer()
    nodes = InspectNodeSerializer(many=True)


class InspectTriggerActionSerializer(serializers.Serializer):
    """An event trigger's action. ``pipeline`` is present only for ``pipeline_start`` actions
    whose (team-scoped) pipeline resolves."""

    type = serializers.CharField()
    params = serializers.DictField(help_text="Action parameters; keys vary by action type.")
    pipeline = InspectPipelineSerializer(required=False)


class InspectStaticTriggerSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    type = serializers.CharField()
    is_active = serializers.BooleanField()
    action = InspectTriggerActionSerializer()


class InspectTimeoutTriggerSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    delay_seconds = serializers.IntegerField()
    total_num_triggers = serializers.IntegerField()
    trigger_from_first_message = serializers.BooleanField()
    is_active = serializers.BooleanField()
    action = InspectTriggerActionSerializer()


class InspectEventsSerializer(serializers.Serializer):
    static_triggers = InspectStaticTriggerSerializer(many=True)
    timeout_triggers = InspectTimeoutTriggerSerializer(many=True)


class ChatbotInspectSerializer(serializers.Serializer):
    """Denormalized, read-only projection of a chatbot's full configuration (ADR-0024). The
    response root *is* the chatbot — there is no wrapper key. Instance:
    :class:`apps.api.v2.inspect.builder.InspectContext`."""

    id = serializers.UUIDField(source="experiment.public_id")
    name = serializers.CharField(source="experiment.name")
    description = serializers.CharField(source="experiment.description", allow_blank=True, allow_null=True)
    version_number = serializers.IntegerField(source="experiment.version_number")
    is_unreleased = serializers.BooleanField(source="experiment.is_working_version")
    is_published_version = serializers.BooleanField(source="experiment.is_default_version")
    version_description = serializers.CharField(source="experiment.version_description", allow_blank=True)
    team_slug = serializers.CharField(source="experiment.team.slug")
    settings = InspectSettingsSerializer(source="experiment")
    consent_form = ConsentFormSerializer(source="experiment.consent_form", allow_null=True)
    pre_survey = SurveySerializer(source="experiment.pre_survey", allow_null=True)
    post_survey = SurveySerializer(source="experiment.post_survey", allow_null=True)
    voice = FlattenedVoiceSerializer(allow_null=True)
    trace_provider = ProviderSerializer(source="experiment.trace_provider", allow_null=True)
    channels = ChannelSerializer(many=True)
    pipeline = InspectPipelineSerializer(allow_null=True)
    events = InspectEventsSerializer()
