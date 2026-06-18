"""Serializers for the chatbot inspect response, built so they never leak secrets.

Every serializer lists its fields explicitly — never ``__all__`` and never a denylist (ADR-0027) —
so adding a field to a model never exposes it here by accident. Encrypted provider ``config``
blobs, signed file-download URLs, and channel ``extra_data`` are all left out.

A provider and its model are combined into one object (``llm`` / ``voice`` / ``embedding``) by the
``Flattened*`` serializers (ADR-0025). They render the resources reached through each node's FK/M2M
relations (preloaded by ``inspect_node_queryset``), so a resource used in several places is simply
repeated wherever it's referenced.
"""

import dataclasses
from functools import cached_property
from typing import TYPE_CHECKING

from django.db.models import Prefetch
from drf_spectacular.extensions import OpenApiSerializerExtension
from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from apps.api.v2.inspect.channels import get_channels
from apps.api.v2.inspect.nodes import (
    graph_digest,
    inspect_node_queryset,
    node_render_order,
)
from apps.api.v2.inspect.param_serializers import node_params_schema
from apps.api.v2.utils import parse_custom_actions
from apps.assistants.models import OpenAiAssistant
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.channels.utils import ALL_DOMAINS
from apps.custom_actions.models import CustomAction
from apps.documents.models import Collection
from apps.events.models import EventAction, EventActionType, StaticTrigger, TimeoutTrigger
from apps.experiments.models import ConsentForm, Experiment, SourceMaterial
from apps.files.models import File
from apps.pipelines.models import Node, Pipeline
from apps.utils.fields import as_int

if TYPE_CHECKING:
    from apps.custom_actions.schema_utils import APIOperationDetails


# Cadence keys exposed for a ``schedule_trigger`` action (resolved Q3).
_CADENCE_KEYS = ("name", "frequency", "time_period", "repetitions", "prompt_text")


@extend_schema_serializer(component_name="InspectFile")
class FileSerializer(serializers.ModelSerializer):
    """A file's identifying fields only.

    Leaves out the signed storage URL (a secret), plus ``summary`` and ``metadata`` (omitted to
    keep the response small, not for secrecy).
    """

    class Meta:
        model = File
        fields = ["id", "name", "content_type", "content_size", "external_source", "external_id"]


class SourceMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceMaterial
        fields = ["id", "topic", "description", "material"]


class ConsentFormSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentForm
        fields = ["id", "name", "consent_text", "capture_identifier", "identifier_label", "identifier_type"]


class AssistantSerializer(serializers.ModelSerializer):
    """Assistant fields. ``instructions`` and ``assistant_id`` are exposed on purpose."""

    class Meta:
        model = OpenAiAssistant
        fields = ["id", "name", "assistant_id", "instructions", "builtin_tools", "tools", "temperature", "top_p"]


class ProviderSerializer(serializers.Serializer):
    """A minimal reference to any provider: its id, type and name.

    It's a plain ``Serializer`` rather than a ``ModelSerializer`` so the same class works for every
    provider model (LLM, voice, messaging, auth, trace). When nested, a ``None`` provider renders as
    ``null``; callers that use it on its own must handle ``None`` themselves.
    """

    id = serializers.IntegerField()
    type = serializers.CharField()
    name = serializers.CharField()


# Sentinel returned by a conditional get_* when a serializer declares a field that doesn't apply to
# this instance; ``to_representation`` drops these so the key is omitted entirely. Distinct from
# None, which renders as null. Used by ChannelSerializer (InspectNodeSerializer drops its own
# conditional keys via has_parameter instead).
_ABSENT = object()


class ChannelSerializer(serializers.ModelSerializer):
    """A channel's ``platform``, ``name``, messaging provider and platform-specific identifier fields.

    ``extra_data`` holds free-form auth material and is excluded entirely. Each platform's non-secret
    identifying field(s) are surfaced as top-level keys, present only for the platform they belong to:
    ``number`` (WhatsApp), ``page_id`` (Facebook), ``sureadhere_tenant_id`` (SureAdhere),
    ``commcare_connect_bot_name`` (CommCare Connect), and ``allow_all_domains`` / ``allowed_domains``
    (Embedded Widget). Platforms whose identifier is itself a secret (Telegram's ``bot_token``, the
    widget's ``widget_token``) surface none.
    """

    # Identifier fields are dropped by ``to_representation`` for platforms that don't use them, so the
    # schema extension marks them optional (see ``_ConditionalRequiredSchemaMixin``).
    CONDITIONAL_RESPONSE_KEYS = (
        "number",
        "page_id",
        "sureadhere_tenant_id",
        "commcare_connect_bot_name",
        "allow_all_domains",
        "allowed_domains",
    )

    messaging_provider = ProviderSerializer(allow_null=True)
    number = serializers.SerializerMethodField()
    page_id = serializers.SerializerMethodField()
    sureadhere_tenant_id = serializers.SerializerMethodField()
    commcare_connect_bot_name = serializers.SerializerMethodField()
    allow_all_domains = serializers.SerializerMethodField()
    allowed_domains = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentChannel
        fields = [
            "platform",
            "name",
            "messaging_provider",
            "number",
            "page_id",
            "sureadhere_tenant_id",
            "commcare_connect_bot_name",
            "allow_all_domains",
            "allowed_domains",
        ]

    def to_representation(self, instance):
        # Each identifier get_* yields _ABSENT for platforms that don't use that field; drop those so
        # the key is omitted entirely (vs. None, which would render as null).
        data = super().to_representation(instance)
        return {key: value for key, value in data.items() if value is not _ABSENT}

    def _identifier(self, channel, platform: ChannelPlatform, key: str):
        """The ``extra_data`` value for ``key``, or ``_ABSENT`` when the channel isn't ``platform``."""
        if channel.platform_enum != platform:
            return _ABSENT
        return (channel.extra_data or {}).get(key)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_number(self, channel):
        return self._identifier(channel, ChannelPlatform.WHATSAPP, "number")

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_page_id(self, channel):
        return self._identifier(channel, ChannelPlatform.FACEBOOK, "page_id")

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_sureadhere_tenant_id(self, channel):
        return self._identifier(channel, ChannelPlatform.SUREADHERE, "sureadhere_tenant_id")

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_commcare_connect_bot_name(self, channel):
        return self._identifier(channel, ChannelPlatform.COMMCARE_CONNECT, "commcare_connect_bot_name")

    @extend_schema_field(serializers.BooleanField())
    def get_allow_all_domains(self, channel):
        if channel.platform_enum != ChannelPlatform.EMBEDDED_WIDGET:
            return _ABSENT
        return ALL_DOMAINS in ((channel.extra_data or {}).get("allowed_domains") or [])

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_allowed_domains(self, channel):
        if channel.platform_enum != ChannelPlatform.EMBEDDED_WIDGET:
            return _ABSENT
        domains = (channel.extra_data or {}).get("allowed_domains") or []
        return [domain for domain in domains if domain != ALL_DOMAINS]


# ── Resolved-instance value objects ──────────────────────────────────────────────────────────────
# Produced by the collector/builder, consumed as serializer instances. They carry loaded model
# objects only; all rendering happens in the serializer classes below.
@dataclasses.dataclass
class ProviderModelPair:
    """A provider paired with its model, behind a flattened ``llm`` or ``embedding`` object."""

    provider: object | None
    model: object | None

    @classmethod
    def from_parts(cls, provider, model) -> "ProviderModelPair | None":
        """Build a pair, or return ``None`` when both halves are missing.

        Returning ``None`` lets the field render as JSON ``null`` instead of an object full of nulls.
        """
        if provider is None and model is None:
            return None
        return cls(provider, model)

    @property
    def type(self):
        """The provider's type, falling back to the model's type when there's no provider."""
        if self.provider is not None:
            return self.provider.type
        return self.model.type if self.model is not None else None


@dataclasses.dataclass
class VoicePair:
    """A voice provider paired with its synthetic voice, behind a flattened ``voice`` object."""

    provider: object | None
    voice: object | None

    @classmethod
    def from_parts(cls, provider, voice) -> "VoicePair | None":
        """Build a pair, or return ``None`` when both halves are missing, so the field renders as null."""
        if provider is None and voice is None:
            return None
        return cls(provider, voice)


@dataclasses.dataclass
class CustomActionSelection:
    """A custom action together with the operation ids selected where it's used."""

    action: CustomAction
    operation_ids: list[str]

    @cached_property
    def resolved_operations(self) -> "list[APIOperationDetails]":
        """The selected operations that still exist in the action's schema.

        Any selected operation that has since been removed from the schema is dropped.
        """
        operations_by_id = self.action.get_operations_by_id()
        return [op for op in (operations_by_id.get(oid) for oid in self.operation_ids) if op]


# ── Flattened / selection serializers (ADR-0025) ─────────────────────────────────────────────────
class FlattenedProviderSerializer(serializers.Serializer):
    """The provider half shared by the flattened pair serializers.

    Renders the pair's provider, or null fields when there's no provider (ADR-0025).
    """

    provider_id = serializers.IntegerField(source="provider.id", default=None, allow_null=True)
    provider_name = serializers.CharField(source="provider.name", default=None, allow_null=True)


class FlattenedModelProviderSerializer(FlattenedProviderSerializer):
    """A collection's embedding provider and model, flattened into one object.

    Expects a :class:`ProviderModelPair`; a missing provider or model renders its fields as null.
    """

    # ``ProviderModelPair.type`` — provider type with model-type fallback.
    type = serializers.CharField(allow_null=True)
    model = serializers.CharField(source="model.name", default=None, allow_null=True)


class FlattenedLlmSerializer(FlattenedModelProviderSerializer):
    """An LLM provider and model, flattened into one ``llm`` object (ADR-0025)."""

    max_token_limit = serializers.IntegerField(
        source="model.max_token_limit",
        default=None,
        allow_null=True,
        help_text="The model's maximum context window, in tokens.",
    )
    deprecated = serializers.BooleanField(
        source="model.deprecated",
        default=None,
        allow_null=True,
        help_text="True if this model is deprecated and should be migrated off.",
    )


class FlattenedVoiceSerializer(FlattenedProviderSerializer):
    """A voice provider and synthetic voice, flattened into one ``voice`` object (ADR-0025).

    Expects a :class:`VoicePair`.
    """

    # Unlike the llm/embedding pairs, ``type`` has no fallback to the voice half — a provider-less
    # voice renders ``type: null`` (matches the pre-refactor shape).
    type = serializers.CharField(
        source="provider.type",
        default=None,
        allow_null=True,
        help_text="Voice provider type; null for a voice with no provider configured.",
    )
    voice_name = serializers.CharField(source="voice.name", default=None, allow_null=True)
    language = serializers.CharField(
        source="voice.language",
        default=None,
        allow_null=True,
        help_text="Voice language/locale code; empty when the voice has no explicit language set.",
    )
    neural = serializers.BooleanField(
        source="voice.neural",
        default=None,
        allow_null=True,
        help_text="Use the provider's neural (higher-quality) voice engine, where supported.",
    )


class MediaCollectionSerializer(serializers.ModelSerializer):
    """A media collection: its identity and files, with no embedding details (ADR-0025)."""

    files = FileSerializer(many=True)

    class Meta:
        model = Collection
        fields = ["id", "name", "files"]


class IndexedCollectionSerializer(MediaCollectionSerializer):
    """An indexed (RAG) collection: its identity, embedding provider/model, and files (ADR-0025)."""

    embedding = serializers.SerializerMethodField()

    class Meta(MediaCollectionSerializer.Meta):
        fields = ["id", "name", "embedding", "files"]

    @extend_schema_field(FlattenedModelProviderSerializer(allow_null=True))
    def get_embedding(self, collection):
        pair = ProviderModelPair.from_parts(collection.llm_provider, collection.embedding_provider_model)
        return FlattenedModelProviderSerializer(pair).data if pair is not None else None


class ApiSchemaDigestSerializer(serializers.Serializer):
    """A custom action's OpenAPI schema reduced to just the paths of the selected operations."""

    paths = serializers.ListField(child=serializers.CharField())


class CustomActionSerializer(serializers.Serializer):
    """A custom action as used at one reference site.

    ``allowed_operations`` lists only the operations selected here, not every operation the action
    defines, and ``api_schema`` is trimmed to just those operations' paths (to keep the response
    small). The auth provider is reduced to ``{id, type, name}`` (ADR-0027). Expects a
    :class:`CustomActionSelection`.
    """

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
class _TeamContextMixin:
    """Gives a serializer access to the request ``team`` in its context.

    A missing team means an inspect serializer is being used outside the inspect view — a
    programming error — so we raise instead of silently mis-scoping.
    """

    @property
    def _team(self):
        try:
            return self.context["team"]
        except KeyError:
            raise RuntimeError(
                f"{type(self).__name__} requires a 'team' in serializer context. Render inspect "
                "serializers via the inspect view (or pass context={'team': ...})."
            ) from None


# ── Settings (ADR-0024) ────────────────────────────────────────────────────────────────────────
class InspectSettingsSerializer(serializers.Serializer):
    """The chatbot's non-secret settings, read directly off the experiment."""

    seed_message = serializers.CharField(
        allow_blank=True, help_text="Message used to start the conversation before the participant says anything."
    )
    conversational_consent_enabled = serializers.BooleanField()
    voice_response_behaviour = serializers.CharField(
        help_text=(
            "When the bot replies with voice: ``always``, ``reciprocal`` (voice only when the "
            "participant sent voice), or ``never``."
        )
    )
    echo_transcript = serializers.BooleanField(
        help_text="When the participant sends a voice message, also reply with the text the bot transcribed."
    )
    debug_mode_enabled = serializers.BooleanField()
    file_uploads_enabled = serializers.BooleanField()
    participant_allowlist = serializers.ListField(
        child=serializers.CharField(),
        help_text="Identifiers permitted to chat; empty means no allowlist restriction.",
    )


# ── Graph (topology digest) ───────────────────────────────────────────────────────────────────
@extend_schema_serializer(component_name="InspectGraphNode")
class GraphNodeSerializer(serializers.Serializer):
    node_id = serializers.CharField(source="flow_id")
    type = serializers.CharField()
    label = serializers.CharField()


@extend_schema_serializer(component_name="InspectGraphEdge")
class GraphEdgeSerializer(serializers.Serializer):
    source = serializers.CharField(help_text="``node_id`` the edge leaves from.")
    target = serializers.CharField(help_text="``node_id`` the edge points to.")
    source_handle = serializers.CharField(
        allow_null=True,
        help_text=(
            "Output handle on the source node. Routers expose one handle per branch "
            "(``output_0``, ``output_1``, …), each mapping by index to the router's ``keywords``; "
            "most nodes have a single ``output``."
        ),
    )
    target_handle = serializers.CharField(
        allow_null=True,
        help_text="Input handle on the target node; currently always null (nodes have one implicit input).",
    )


@extend_schema_serializer(component_name="InspectGraph")
class GraphSerializer(serializers.Serializer):
    """The pipeline's shape: its nodes and the edges between them (without canvas positions).

    This is the topology only — each node carries just ``node_id``/``type``/``label`` for drawing the
    DAG. The sibling ``nodes`` list on the pipeline holds the same nodes in render order with their
    full configuration.
    """

    nodes = GraphNodeSerializer(many=True)
    edges = GraphEdgeSerializer(many=True)


# ── Node (ADR-0025) ──────────────────────────────────────────────────────────────────────────────
class InspectNodeSerializer(serializers.ModelSerializer):
    """One pipeline node, with the resources it references inlined.

    All resource keys are declared as fields so they appear in the OpenAPI schema, but a node only
    renders the keys its type actually uses: a ``StartNode`` shows none, while an
    ``LLMResponseWithPrompt`` shows its own — ``null`` for an unset single value, ``[]`` for an
    unset list. Keys the node type doesn't declare are dropped entirely by ``to_representation``.

    Simple references (``source_material``, ``assistant``, ``media_collection``,
    ``indexed_collections``) are declarative nested fields mapped straight to the node's FK/M2M
    relations. ``llm``/``voice`` stay method fields — they flatten two source objects into one and
    render ``null`` when empty — as does ``custom_actions``, which groups operations by action.
    """

    # The resource keys ``to_representation`` drops for node types that don't declare them: they're
    # documented in the schema but not guaranteed present, so the schema extension marks them
    # optional (see ``_ConditionalRequiredSchemaMixin``).
    CONDITIONAL_RESPONSE_KEYS = (
        "llm",
        "voice",
        "source_material",
        "assistant",
        "custom_actions",
        "media_collection",
        "indexed_collections",
    )
    _CONDITIONAL_KEY_PARAMS = {
        "llm": ("llm_provider_id", "llm_provider_model_id"),
        "voice": ("synthetic_voice_id",),
        "source_material": ("source_material_id",),
        "assistant": ("assistant_id",),
        "custom_actions": ("custom_actions",),
        "media_collection": ("collection_id",),
        "indexed_collections": ("collection_index_ids",),
    }
    _RESOURCE_PARAM_KEYS = frozenset(param for params in _CONDITIONAL_KEY_PARAMS.values() for param in params)

    node_id = serializers.CharField(source="flow_id")
    type = serializers.CharField()
    label = serializers.CharField()
    params = serializers.SerializerMethodField(
        help_text="The node's non-resource configuration, verbatim; keys vary by node type."
    )
    llm = serializers.SerializerMethodField()
    voice = serializers.SerializerMethodField()
    custom_actions = serializers.SerializerMethodField()
    source_material = SourceMaterialSerializer(allow_null=True, read_only=True)
    assistant = AssistantSerializer(allow_null=True, read_only=True)
    media_collection = MediaCollectionSerializer(source="collection", allow_null=True, read_only=True)
    indexed_collections = IndexedCollectionSerializer(source="collection_indexes", many=True, read_only=True)

    class Meta:
        model = Node
        # Explicit so the rendered shape is readable at a glance. These resource keys are render
        # concepts owned here; they map to the node's FK/M2M relations (see _CONDITIONAL_KEY_PARAMS).
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
        # A node only renders the resource keys whose backing param its type declares; drop the rest
        # entirely (vs. None -> declared-but-unset null).
        data = super().to_representation(instance)
        for key, params in self._CONDITIONAL_KEY_PARAMS.items():
            if key in data and not any(instance.has_parameter(param) for param in params):
                del data[key]
        return data

    @extend_schema_field(node_params_schema())
    def get_params(self, node) -> dict:
        # Resource ids are surfaced under their own keys, never echoed in params (and "name" is the
        # node label, exposed separately).
        params = {k: v for k, v in (node.params or {}).items() if k not in self._RESOURCE_PARAM_KEYS and k != "name"}
        # ``max_results`` only bounds index search, so surface it under a clearer name.
        if "max_results" in params:
            params["max_indexed_collection_search_results"] = params.pop("max_results")
        return params

    @extend_schema_field(FlattenedLlmSerializer(allow_null=True))
    def get_llm(self, node):
        pair = ProviderModelPair.from_parts(node.llm_provider, node.llm_provider_model)
        return FlattenedLlmSerializer(pair).data if pair is not None else None

    @extend_schema_field(FlattenedVoiceSerializer(allow_null=True))
    def get_voice(self, node):
        voice = node.synthetic_voice
        if voice is None:
            return None
        return FlattenedVoiceSerializer(VoicePair(voice.voice_provider, voice)).data

    @extend_schema_field(CustomActionSerializer(many=True))
    def get_custom_actions(self, node):
        # The CustomAction objects come from the prefetched relation; params is parsed only to keep
        # the selected operation_ids in their saved order (resolved_operations drops any no longer in
        # the action's schema).
        actions_by_id = {op.custom_action_id: op.custom_action for op in node.custom_action_operations.all()}
        selections = []
        for action_id, operation_ids in parse_custom_actions(node.params.get("custom_actions")):
            action = actions_by_id.get(action_id)
            if action is not None:
                selections.append(CustomActionSelection(action, operation_ids))
        return CustomActionSerializer(selections, many=True).data


# ── Pipeline ─────────────────────────────────────────────────────────────────────────────────────
class InspectPipelineSerializer(serializers.ModelSerializer):
    graph = serializers.SerializerMethodField(
        help_text="The pipeline topology (nodes as id/type/label, plus edges) for drawing the DAG."
    )
    nodes = serializers.SerializerMethodField(
        help_text="The same nodes as ``graph``, in render order, each with its full configuration."
    )

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
class InspectTriggerActionSerializer(_TeamContextMixin, serializers.ModelSerializer):
    """The action an event trigger runs.

    The ``pipeline`` key only appears for ``pipeline_start`` actions whose pipeline exists in the
    team. Its ``pipeline_id`` lives in the action's JSON params (not an FK), so it's loaded with a
    single team-scoped query, prefetched the same way as the chatbot's own pipeline.
    """

    # ``pipeline`` is dropped by ``to_representation`` for non-pipeline_start actions, so the schema
    # extension marks it optional (see ``_ConditionalRequiredSchemaMixin``).
    CONDITIONAL_RESPONSE_KEYS = ("pipeline",)

    params = serializers.SerializerMethodField(help_text="Action parameters; keys depend on the action ``type``.")
    pipeline = serializers.SerializerMethodField()

    class Meta:
        model = EventAction
        fields = ["type", "params", "pipeline"]
        extra_kwargs = {"type": {"help_text": "What the trigger runs", "source": "action_type"}}

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
        pipeline_id = as_int((action.params or {}).get("pipeline_id"))
        if pipeline_id is None:
            return None
        pipeline = (
            Pipeline.objects.filter(team=self._team, id=pipeline_id)
            .prefetch_related(Prefetch("node_set", queryset=inspect_node_queryset()))
            .first()
        )
        if pipeline is None:
            return None
        return InspectPipelineSerializer(pipeline, context=self.context).data


class InspectStaticTriggerSerializer(serializers.ModelSerializer):
    action = InspectTriggerActionSerializer()

    class Meta:
        model = StaticTrigger
        fields = ["id", "type", "is_active", "action"]
        extra_kwargs = {"type": {"help_text": "The conversation event that fires this trigger"}}


class InspectTimeoutTriggerSerializer(serializers.ModelSerializer):
    delay_seconds = serializers.IntegerField(
        source="delay", help_text="Seconds of inactivity before the trigger fires."
    )
    total_num_triggers = serializers.IntegerField(
        help_text="Maximum number of times this timeout fires within a session."
    )
    trigger_from_first_message = serializers.BooleanField(
        help_text="Measure the delay from the first message (true) rather than the most recent message (false)."
    )
    action = InspectTriggerActionSerializer()

    class Meta:
        model = TimeoutTrigger
        fields = ["id", "delay_seconds", "total_num_triggers", "trigger_from_first_message", "is_active", "action"]


class InspectEventsSerializer(serializers.Serializer):
    """A chatbot's static and timeout triggers, excluding archived ones.

    ``static_triggers`` fire on conversation events (start, end, a new message, …); ``timeout_triggers``
    fire after a period of inactivity. The two overlap at the end of a conversation: a ``last_timeout``
    static trigger fires once the final timeout has elapsed.
    """

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
    """The whole chatbot configuration in one read-only response (ADR-0024).

    The chatbot's fields sit at the top level — there's no wrapper key around them. Expects the
    resolved :class:`~apps.experiments.models.Experiment`, and needs a ``fetcher`` in its context.
    """

    id = serializers.UUIDField(source="public_id")
    is_unreleased = serializers.BooleanField(
        source="is_working_version",
        help_text="True for the working (draft) version that has not been released; never the published one.",
    )
    is_published_version = serializers.BooleanField(
        source="is_default_version",
        help_text="True for the version currently served to participants (the live default).",
    )
    version_description = serializers.CharField(
        allow_blank=True, help_text="Free-text label for this version (e.g. 'latest'); not an enum."
    )
    team_slug = serializers.CharField(source="team.slug")
    settings = InspectSettingsSerializer(source="*")
    consent_form = ConsentFormSerializer(allow_null=True)
    trace_provider = ProviderSerializer(allow_null=True)
    voice = serializers.SerializerMethodField()
    channels = serializers.SerializerMethodField()
    pipeline = InspectPipelineSerializer(allow_null=True, read_only=True)
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


# ── Schema extensions ──────────────────────────────────────────────────────────────────────────
# Read-only fields land in a component's ``required`` list by default (drf-spectacular treats every
# read-only field as always-present). The inspect serializers above intentionally omit some keys per
# instance via ``to_representation``, so those keys must be optional in the schema or generated
# clients reject valid responses (e.g. a ``StartNode`` carries none of the resource keys). These
# extensions drop each serializer's ``CONDITIONAL_RESPONSE_KEYS`` from its generated ``required``.
class _ConditionalRequiredSchemaMixin:
    """Removes a serializer's ``CONDITIONAL_RESPONSE_KEYS`` from its generated ``required`` list."""

    def map_serializer(self, auto_schema, direction):
        schema = super().map_serializer(auto_schema, direction)
        optional = set(self.target.CONDITIONAL_RESPONSE_KEYS)
        if optional and "required" in schema:
            schema["required"] = [name for name in schema["required"] if name not in optional]
            if not schema["required"]:
                del schema["required"]
        return schema


class ChannelSchemaExtension(_ConditionalRequiredSchemaMixin, OpenApiSerializerExtension):
    target_class = "apps.api.v2.inspect.serializers.ChannelSerializer"


class InspectNodeSchemaExtension(_ConditionalRequiredSchemaMixin, OpenApiSerializerExtension):
    target_class = "apps.api.v2.inspect.serializers.InspectNodeSerializer"


class InspectTriggerActionSchemaExtension(_ConditionalRequiredSchemaMixin, OpenApiSerializerExtension):
    target_class = "apps.api.v2.inspect.serializers.InspectTriggerActionSerializer"
