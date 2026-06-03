"""Team-scoped resource collector + inliner for the inspect projection (ADR-0028, design step 7).

The node and event walkers accumulate a ``resource_kind -> ids`` map. The ids originate in
untrusted node-parameter JSON, so every batch load is **team-scoped**: a stray or crafted
cross-team id resolves to absent rather than leaking another team's resource. Each resource type is
loaded exactly once (no N+1, regardless of fan-out), then the loaded instance is handed to every
reference site for the response serializers to render (ADR-0025); duplication across sites is by design.
"""

from typing import assert_never

from django.db.models import Q

from apps.api.v2.inspect.node_walker import (
    CustomActionsRef,
    ListRef,
    LlmRef,
    Ref,
    ResourceKind,
    ResourceRefMap,
    SingleRef,
    VoiceRef,
    merge_refs,
)
from apps.api.v2.inspect.serializers import CustomActionSelection, ProviderModelPair, VoicePair
from apps.assistants.models import OpenAiAssistant
from apps.custom_actions.models import CustomAction
from apps.documents.models import Collection
from apps.experiments.models import SourceMaterial, SyntheticVoice
from apps.service_providers.models import LlmProvider, LlmProviderModel, VoiceProvider


class InspectCollector:
    """Batch-loads team-scoped resources, then hands the loaded instances to every reference site
    for the response serializers to render."""

    def __init__(self, team):
        self.team = team
        self._objects: dict[ResourceKind, dict[int, object]] = {}

    def load(self, *resource_ref_maps: ResourceRefMap) -> "InspectCollector":
        """Batch-load every referenced resource once. Accepts one or more ``kind -> ids`` maps
        (e.g. from the top-level pipeline walk and the events walk)."""
        merged: ResourceRefMap = {}
        for ref_map in resource_ref_maps:
            merge_refs(merged, ref_map)
        for kind, ids in merged.items():
            ids.discard(None)
            queryset = self._queryset(kind, ids)
            self._objects[kind] = {obj.id: obj for obj in queryset} if queryset is not None else {}
        return self

    def _queryset(self, kind: ResourceKind, ids: set[int]):
        if not ids:
            return None
        team = self.team
        if kind == ResourceKind.SOURCE_MATERIAL:
            return SourceMaterial.objects.filter(team=team, id__in=ids)
        if kind == ResourceKind.ASSISTANT:
            return OpenAiAssistant.objects.filter(team=team, id__in=ids)
        if kind == ResourceKind.CUSTOM_ACTION:
            return CustomAction.objects.filter(team=team, id__in=ids).select_related("auth_provider")
        if kind == ResourceKind.COLLECTION:
            return (
                Collection.objects.filter(team=team, id__in=ids)
                .select_related("llm_provider", "embedding_provider_model")
                .prefetch_related("files")
            )
        if kind == ResourceKind.LLM_PROVIDER:
            return LlmProvider.objects.filter(team=team, id__in=ids)
        if kind == ResourceKind.LLM_PROVIDER_MODEL:
            # LlmProviderModel allows global rows with a null team (shared across teams).
            return LlmProviderModel.objects.filter(Q(team=team) | Q(team__isnull=True), id__in=ids)
        if kind == ResourceKind.VOICE_PROVIDER:
            return VoiceProvider.objects.filter(team=team, id__in=ids)
        if kind == ResourceKind.SYNTHETIC_VOICE:
            # SyntheticVoice is a global (non-team-scoped) catalogue row; safe to load by id.
            return SyntheticVoice.objects.filter(id__in=ids).select_related("voice_provider")
        # Exhaustiveness guard: a new ResourceKind without a team-scoped queryset here is a type
        # error (and raises at runtime) instead of silently loading nothing.
        assert_never(kind)

    def _get(self, kind: ResourceKind, resource_id) -> object | None:
        if not resource_id:
            return None
        # pipeline.params contains string ids whereas self._objects has int ids.
        return self._objects.get(kind, {}).get(int(resource_id))

    def resolve_refs(self, refs: dict[str, Ref]) -> dict:
        """Resolve a walker's ``refs`` map (payload_key -> ref) to loaded instances / pairs.

        No serialization happens here — the response serializers render these. ``None`` means the
        reference is unset or resolved to absent (cross-team / deleted id, ADR-0028)."""
        return {key: self._resolve(ref) for key, ref in refs.items()}

    def _resolve(self, ref: Ref) -> object | None:
        if isinstance(ref, LlmRef):
            return ProviderModelPair.from_parts(
                self._get(ResourceKind.LLM_PROVIDER, ref.provider_id),
                self._get(ResourceKind.LLM_PROVIDER_MODEL, ref.model_id),
            )
        if isinstance(ref, VoiceRef):
            voice = self._get(ResourceKind.SYNTHETIC_VOICE, ref.synthetic_voice_id)
            if voice is None:
                return None
            return VoicePair(voice.voice_provider, voice)
        if isinstance(ref, SingleRef):
            return self._wrap_single(ref.kind, self._get(ref.kind, ref.id))
        if isinstance(ref, ListRef):
            resolved = (self._wrap_single(ref.kind, self._get(ref.kind, rid)) for rid in ref.ids)
            return [obj for obj in resolved if obj is not None]
        if isinstance(ref, CustomActionsRef):
            selections = ((self._get(ResourceKind.CUSTOM_ACTION, aid), ops) for aid, ops in ref.selections)
            return [CustomActionSelection(action, ops) for action, ops in selections if action]
        # Exhaustiveness guard: a new ``Ref`` union member unhandled here is a type error (and
        # raises at runtime) instead of silently resolving to None.
        assert_never(ref)

    @staticmethod
    def _wrap_single(kind: ResourceKind, obj) -> object | None:
        """Wrap a resolved instance in the value object its payload key's serializer expects.

        The voice kinds render under the ``voice`` payload key through ``FlattenedVoiceSerializer``,
        which reads a :class:`VoicePair` — a raw provider/voice instance would silently render
        all-null. Only the forward-compat ``OptionsSource`` entries produce these as SingleRefs
        today (the voice widget path goes through ``VoiceRef``)."""
        if obj is None:
            return None
        if kind == ResourceKind.SYNTHETIC_VOICE:
            return VoicePair(obj.voice_provider, obj)
        if kind == ResourceKind.VOICE_PROVIDER:
            return VoicePair(obj, None)
        return obj
