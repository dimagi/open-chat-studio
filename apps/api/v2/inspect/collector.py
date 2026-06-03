"""Team-scoped resource collector + inliner for the inspect projection (ADR-0028, design step 7).

The node and event walkers accumulate a ``resource_kind -> ids`` map. The ids originate in
untrusted node-parameter JSON, so every batch load is **team-scoped**: a stray or crafted
cross-team id resolves to absent rather than leaking another team's resource. Each resource type is
loaded exactly once (no N+1, regardless of fan-out), then the serialized object is inlined — copied
— at every reference site (ADR-0025); duplication across sites is by design.
"""

from django.db.models import Q

from apps.api.v2.inspect.node_walker import (
    ASSISTANT,
    COLLECTION,
    CUSTOM_ACTION,
    LLM_PROVIDER,
    LLM_PROVIDER_MODEL,
    SOURCE_MATERIAL,
    SYNTHETIC_VOICE,
    VOICE_PROVIDER,
    ListRef,
    LlmRef,
    SingleRef,
    VoiceRef,
)
from apps.api.v2.inspect.serializers import (
    AssistantSerializer,
    SourceMaterialSerializer,
    serialize_collection,
    serialize_custom_action,
    serialize_llm_model,
    serialize_synthetic_voice,
)
from apps.assistants.models import OpenAiAssistant
from apps.custom_actions.models import CustomAction
from apps.documents.models import Collection
from apps.experiments.models import SourceMaterial, SyntheticVoice
from apps.service_providers.models import LlmProvider, LlmProviderModel, VoiceProvider


class InspectCollector:
    """Batch-loads team-scoped resources, then inlines serialized copies at each reference site."""

    def __init__(self, team):
        self.team = team
        self._objects: dict[str, dict[int, object]] = {}

    def load(self, *resource_ref_maps: dict[str, set[int]]) -> "InspectCollector":
        """Batch-load every referenced resource once. Accepts one or more ``kind -> ids`` maps
        (e.g. from the top-level pipeline walk and the events walk)."""
        merged: dict[str, set[int]] = {}
        for ref_map in resource_ref_maps:
            for kind, ids in ref_map.items():
                merged.setdefault(kind, set()).update(i for i in ids if i is not None)
        for kind, ids in merged.items():
            queryset = self._queryset(kind, ids)
            self._objects[kind] = {obj.id: obj for obj in queryset} if queryset is not None else {}
        return self

    def _queryset(self, kind: str, ids: set[int]):
        if not ids:
            return None
        team = self.team
        if kind == SOURCE_MATERIAL:
            return SourceMaterial.objects.filter(team=team, id__in=ids)
        if kind == ASSISTANT:
            return OpenAiAssistant.objects.filter(team=team, id__in=ids)
        if kind == CUSTOM_ACTION:
            return CustomAction.objects.filter(team=team, id__in=ids).select_related("auth_provider")
        if kind == COLLECTION:
            return (
                Collection.objects.filter(team=team, id__in=ids)
                .select_related("llm_provider", "embedding_provider_model")
                .prefetch_related("files")
            )
        if kind == LLM_PROVIDER:
            return LlmProvider.objects.filter(team=team, id__in=ids)
        if kind == LLM_PROVIDER_MODEL:
            # LlmProviderModel allows global rows with a null team (shared across teams).
            return LlmProviderModel.objects.filter(Q(team=team) | Q(team__isnull=True), id__in=ids)
        if kind == VOICE_PROVIDER:
            return VoiceProvider.objects.filter(team=team, id__in=ids)
        if kind == SYNTHETIC_VOICE:
            # SyntheticVoice is a global (non-team-scoped) catalogue row; safe to load by id.
            return SyntheticVoice.objects.filter(id__in=ids).select_related("voice_provider")
        return None

    def _get(self, kind: str, resource_id) -> object | None:
        if not resource_id:
            return None
        # pipeline.params contains string ids whereas self._objects has int ids.
        return self._objects.get(kind, {}).get(int(resource_id))

    def inline_refs(self, refs: dict[str, object]) -> dict:
        """Render a walker's ``refs`` map (payload_key -> ref) into inline serialized objects."""
        return {key: self._render(key, ref) for key, ref in refs.items()}

    def _render(self, payload_key: str, ref) -> object:
        if isinstance(ref, LlmRef):
            return serialize_llm_model(
                self._get(LLM_PROVIDER, ref.provider_id), self._get(LLM_PROVIDER_MODEL, ref.model_id)
            )
        if isinstance(ref, VoiceRef):
            voice = self._get(SYNTHETIC_VOICE, ref.synthetic_voice_id)
            return serialize_synthetic_voice(getattr(voice, "voice_provider", None), voice)
        if isinstance(ref, SingleRef):
            return self._serialize(payload_key, ref.kind, self._get(ref.kind, ref.id))
        if isinstance(ref, ListRef):
            rendered = [self._serialize(payload_key, ref.kind, self._get(ref.kind, rid)) for rid in ref.ids]
            return [item for item in rendered if item is not None]
        return None

    def _serialize(self, payload_key: str, kind: str, obj) -> dict | None:
        if obj is None:
            # A cross-team or deleted id resolves to absent rather than leaking (ADR-0028).
            return None
        if kind == COLLECTION:
            # The payload key distinguishes a media collection (no embedding) from a RAG index.
            return serialize_collection(obj, with_embedding=payload_key == "indexed_collections")
        if kind == SOURCE_MATERIAL:
            return SourceMaterialSerializer(obj).data
        if kind == ASSISTANT:
            return AssistantSerializer(obj).data
        if kind == CUSTOM_ACTION:
            return serialize_custom_action(obj)
        return None
