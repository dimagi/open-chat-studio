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

from apps.api.v2.inspect.channels import get_channels
from apps.api.v2.inspect.nodes import (
    RESOURCE_PARAM_FIELDS,
    graph_digest,
    node_render_order,
)
from apps.api.v2.utils import parse_custom_actions
from apps.assistants.models import OpenAiAssistant
from apps.channels.models import ExperimentChannel
from apps.custom_actions.models import CustomAction
from apps.documents.models import Collection
from apps.events.models import EventAction, EventActionType, StaticTrigger, TimeoutTrigger
from apps.experiments.models import ConsentForm, Experiment, SourceMaterial
from apps.files.models import File
from apps.pipelines.models import Node, Pipeline

if TYPE_CHECKING:
    from apps.custom_actions.schema_utils import APIOperationDetails


# Cadence keys exposed for a ``schedule_trigger`` action (resolved Q3).
_CADENCE_KEYS = ("name", "frequency", "time_period", "repetitions", "prompt_text")


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


# ── Context helper ───────────────────────────────────────────────────────────────────────────────
class _FetcherContextMixin:
    """Inspect serializers that resolve resources read the batch-loaded ``ResourceFetcher`` from
    context. A missing fetcher is a programming error (an inspect serializer rendered outside the
    inspect view), so fail loud rather than papering over it."""

    @property
    def _fetcher(self):
        try:
            return self.context["fetcher"]
        except KeyError:
            raise RuntimeError(
                f"{type(self).__name__} requires a 'fetcher' in serializer context. Render inspect "
                "serializers via the inspect view (or pass context={'fetcher': ...})."
            ) from None


# ── Settings (ADR-0024) ────────────────────────────────────────────────────────────────────────
class InspectSettingsSerializer(serializers.Serializer):
    """Non-secret Experiment fields surfaced under ``settings``, sourced straight off the
    experiment instance (``source="*"`` at the parent)."""

    seed_message = serializers.CharField(allow_blank=True)
    conversational_consent_enabled = serializers.BooleanField()
    voice_response_behaviour = serializers.CharField()
    echo_transcript = serializers.BooleanField()
    use_processor_bot_voice = serializers.BooleanField()
    debug_mode_enabled = serializers.BooleanField()
    file_uploads_enabled = serializers.BooleanField()
    participant_allowlist = serializers.ListField(child=serializers.CharField())


# ── Graph (topology digest) ───────────────────────────────────────────────────────────────────
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


# ── Node (ADR-0025) ──────────────────────────────────────────────────────────────────────────────
# Returned by a resource get_* when the node TYPE doesn't declare its backing param field(s);
# to_representation drops these so the key is omitted. Distinct from None, which renders as null.
_ABSENT = object()


class InspectNodeSerializer(_FetcherContextMixin, serializers.ModelSerializer):
    """One pipeline node with its declared resource references inlined.

    The seven resource keys are declared as fields so the OpenAPI schema documents them, but only
    the keys the node *type* declares are rendered (decision #5): a ``StartNode`` carries no
    resource keys, an ``LLMResponseWithPrompt`` always carries all six of its own, with ``null``
    (single) / ``[]`` (list) where unset. Each ``get_*`` returns ``_ABSENT`` for a key its node
    type doesn't declare, and ``to_representation`` drops those — so the declared-vs-absent rule
    lives next to each field's fetch, not in a central key list."""

    node_id = serializers.CharField(source="flow_id")
    type = serializers.CharField()
    label = serializers.CharField()
    params = serializers.SerializerMethodField(
        help_text="The node's non-resource configuration, verbatim; keys vary by node type."
    )
    llm = serializers.SerializerMethodField()
    voice = serializers.SerializerMethodField()
    source_material = serializers.SerializerMethodField()
    assistant = serializers.SerializerMethodField()
    custom_actions = serializers.SerializerMethodField()
    media_collection = serializers.SerializerMethodField()
    indexed_collections = serializers.SerializerMethodField()

    class Meta:
        model = Node
        # Explicit so the rendered shape is readable at a glance. These resource keys are render
        # concepts owned here; their backing param fields live in RESOURCE_PARAM_FIELDS.
        fields = [
            "node_id",
            "type",
            "label",
            "params",
            "llm",
            "voice",
            "source_material",
            "assistant",
            "custom_actions",
            "media_collection",
            "indexed_collections",
        ]

    def to_representation(self, instance):
        # Each resource get_* yields _ABSENT when this node type doesn't declare its backing field(s);
        # drop those so the key is omitted (vs. None -> declared-but-unset null, decision #5).
        data = super().to_representation(instance)
        return {key: value for key, value in data.items() if value is not _ABSENT}

    @extend_schema_field(serializers.DictField())
    def get_params(self, node) -> dict:
        # Resource ids are surfaced under their own keys, never echoed in params (and "name" is the
        # node label, exposed separately) — strip every known resource field, declared or not.
        return {k: v for k, v in (node.params or {}).items() if k not in RESOURCE_PARAM_FIELDS and k != "name"}

    @extend_schema_field(FlattenedLlmSerializer(allow_null=True))
    def get_llm(self, node):
        if not (node.has_parameter("llm_provider_id") or node.has_parameter("llm_provider_model_id")):
            return _ABSENT
        pair = ProviderModelPair.from_parts(
            self._fetcher.llm_provider(node.params.get("llm_provider_id")),
            self._fetcher.llm_provider_model(node.params.get("llm_provider_model_id")),
        )
        return FlattenedLlmSerializer(pair).data if pair is not None else None

    @extend_schema_field(FlattenedVoiceSerializer(allow_null=True))
    def get_voice(self, node):
        if not node.has_parameter("synthetic_voice_id"):
            return _ABSENT
        voice = self._fetcher.synthetic_voice(node.params.get("synthetic_voice_id"))
        if voice is None:
            return None
        return FlattenedVoiceSerializer(VoicePair(voice.voice_provider, voice)).data

    @extend_schema_field(SourceMaterialSerializer(allow_null=True))
    def get_source_material(self, node):
        if not node.has_parameter("source_material_id"):
            return _ABSENT
        material = self._fetcher.source_material(node.params.get("source_material_id"))
        return SourceMaterialSerializer(material).data if material is not None else None

    @extend_schema_field(AssistantSerializer(allow_null=True))
    def get_assistant(self, node):
        if not node.has_parameter("assistant_id"):
            return _ABSENT
        assistant = self._fetcher.assistant(node.params.get("assistant_id"))
        return AssistantSerializer(assistant).data if assistant is not None else None

    @extend_schema_field(CustomActionSerializer(many=True))
    def get_custom_actions(self, node):
        if not node.has_parameter("custom_actions"):
            return _ABSENT
        selections = []
        for action_id, operation_ids in parse_custom_actions(node.params.get("custom_actions")):
            action = self._fetcher.custom_action(action_id)
            if action is not None:
                selections.append(CustomActionSelection(action, operation_ids))
        return CustomActionSerializer(selections, many=True).data

    @extend_schema_field(MediaCollectionSerializer(allow_null=True))
    def get_media_collection(self, node):
        if not node.has_parameter("collection_id"):
            return _ABSENT
        collection = self._fetcher.collection(node.params.get("collection_id"))
        return MediaCollectionSerializer(collection).data if collection is not None else None

    @extend_schema_field(IndexedCollectionSerializer(many=True))
    def get_indexed_collections(self, node):
        if not node.has_parameter("collection_index_ids"):
            return _ABSENT
        collections = [
            collection
            for raw_id in (node.params.get("collection_index_ids") or [])
            if (collection := self._fetcher.collection(raw_id)) is not None
        ]
        return IndexedCollectionSerializer(collections, many=True).data


# ── Pipeline ─────────────────────────────────────────────────────────────────────────────────────
class InspectPipelineSerializer(serializers.ModelSerializer):
    graph = serializers.SerializerMethodField()
    nodes = serializers.SerializerMethodField()

    class Meta:
        model = Pipeline
        fields = ["id", "name", "version_number", "graph", "nodes"]

    @extend_schema_field(GraphSerializer())
    def get_graph(self, pipeline) -> dict:
        # The graph digest is a topology keyed by flow_id/edges, so node order is immaterial here;
        # the human-facing ``nodes`` list below is the one that is render-ordered. Rendered through
        # GraphSerializer so the digest's ``flow_id`` is exposed as ``node_id`` (matching the graph
        # node shape) rather than leaking the raw column name.
        return GraphSerializer(graph_digest(list(pipeline.node_set.all()), pipeline.data)).data

    @extend_schema_field(InspectNodeSerializer(many=True))
    def get_nodes(self, pipeline) -> list:
        nodes = sorted(pipeline.node_set.all(), key=lambda n: (node_render_order(n), n.id))
        return InspectNodeSerializer(nodes, many=True, context=self.context).data


# ── Events / triggers / actions (ADR-0025) ───────────────────────────────────────────────────────
class InspectTriggerActionSerializer(_FetcherContextMixin, serializers.ModelSerializer):
    """An event trigger's action. ``pipeline`` is present only for ``pipeline_start`` actions
    whose (team-scoped) pipeline resolves; it is rendered from the fetcher's embedded-pipeline
    cache so it costs no extra query."""

    type = serializers.CharField(source="action_type")
    params = serializers.SerializerMethodField()
    pipeline = serializers.SerializerMethodField()

    class Meta:
        model = EventAction
        fields = ["type", "params", "pipeline"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("pipeline") is None:
            data.pop("pipeline", None)
        return data

    @extend_schema_field(serializers.DictField())
    def get_params(self, action) -> dict:
        params = dict(action.params or {})
        if action.action_type == EventActionType.SCHEDULETRIGGER:
            return {"scheduled_message": {key: params.get(key) for key in _CADENCE_KEYS}}
        if action.action_type == EventActionType.PIPELINE_START:
            params.pop("pipeline_id", None)
        return params

    @extend_schema_field(InspectPipelineSerializer(required=False))
    def get_pipeline(self, action):
        if action.action_type != EventActionType.PIPELINE_START:
            return None
        pipeline = self._fetcher.embedded_pipeline((action.params or {}).get("pipeline_id"))
        if pipeline is None:
            return None
        return InspectPipelineSerializer(pipeline, context=self.context).data


class InspectStaticTriggerSerializer(serializers.ModelSerializer):
    action = InspectTriggerActionSerializer()

    class Meta:
        model = StaticTrigger
        fields = ["id", "type", "is_active", "action"]


class InspectTimeoutTriggerSerializer(serializers.ModelSerializer):
    delay_seconds = serializers.IntegerField(source="delay")
    action = InspectTriggerActionSerializer()

    class Meta:
        model = TimeoutTrigger
        fields = ["id", "delay_seconds", "total_num_triggers", "trigger_from_first_message", "is_active", "action"]


class InspectEventsSerializer(serializers.Serializer):
    """Static + timeout triggers (archived excluded). ``source="*"`` at the parent — the instance
    is the Experiment."""

    static_triggers = serializers.SerializerMethodField()
    timeout_triggers = serializers.SerializerMethodField()

    @extend_schema_field(InspectStaticTriggerSerializer(many=True))
    def get_static_triggers(self, experiment) -> list:
        triggers = [t for t in experiment.static_triggers.all() if not t.is_archived]
        return InspectStaticTriggerSerializer(triggers, many=True, context=self.context).data

    @extend_schema_field(InspectTimeoutTriggerSerializer(many=True))
    def get_timeout_triggers(self, experiment) -> list:
        triggers = [t for t in experiment.timeout_triggers.all() if not t.is_archived]
        return InspectTimeoutTriggerSerializer(triggers, many=True, context=self.context).data


# ── Root (ADR-0024) ──────────────────────────────────────────────────────────────────────────────
class ChatbotInspectSerializer(serializers.ModelSerializer):
    """Denormalized, read-only projection of a chatbot's full configuration (ADR-0024). The
    response root *is* the chatbot — there is no wrapper key. Instance: the resolved
    :class:`~apps.experiments.models.Experiment`; requires a ``fetcher`` in context."""

    id = serializers.UUIDField(source="public_id")
    is_unreleased = serializers.BooleanField(source="is_working_version")
    is_published_version = serializers.BooleanField(source="is_default_version")
    version_description = serializers.CharField(allow_blank=True)
    team_slug = serializers.CharField(source="team.slug")
    settings = InspectSettingsSerializer(source="*")
    consent_form = ConsentFormSerializer(allow_null=True)
    trace_provider = ProviderSerializer(allow_null=True)
    voice = serializers.SerializerMethodField()
    channels = serializers.SerializerMethodField()
    pipeline = serializers.SerializerMethodField()
    events = InspectEventsSerializer(source="*")

    class Meta:
        model = Experiment
        fields = [
            "id",
            "name",
            "description",
            "version_number",
            "is_unreleased",
            "is_published_version",
            "version_description",
            "team_slug",
            "settings",
            "consent_form",
            "voice",
            "trace_provider",
            "channels",
            "pipeline",
            "events",
        ]

    @extend_schema_field(FlattenedVoiceSerializer(allow_null=True))
    def get_voice(self, experiment):
        pair = VoicePair.from_parts(experiment.voice_provider, experiment.synthetic_voice)
        return FlattenedVoiceSerializer(pair).data if pair is not None else None

    @extend_schema_field(ChannelSerializer(many=True))
    def get_channels(self, experiment) -> list:
        return ChannelSerializer(get_channels(experiment), many=True).data

    @extend_schema_field(InspectPipelineSerializer(allow_null=True))
    def get_pipeline(self, experiment):
        if experiment.pipeline_id is None:
            return None
        return InspectPipelineSerializer(experiment.pipeline, context=self.context).data
