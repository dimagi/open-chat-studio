"""Team-scoped batch loader + id-collection traversal for the inspect projection (ADR-0028).

``iter_resource_refs`` is the single definition of "which node params are resources, by kind". It
feeds ``ResourceFetcher.for_experiment``'s pre-pass, which batch-loads every kind once
(N+1-free, team-scoped) into by-id maps. The serializers read the loaded instances via the
accessors — pure dict lookups, never queries. Ids originate in untrusted node-param JSON, so
``_as_int`` coercion drops malformed values (a bad id resolves to absent, never crashes).
"""

import collections
from typing import assert_never

from django.db.models import Q

from apps.api.v2.inspect.nodes import RESOURCE_FIELDS, ResourceKind, declared_resource_keys, node_class_for
from apps.assistants.models import OpenAiAssistant
from apps.custom_actions.models import CustomAction
from apps.documents.models import Collection
from apps.events.models import EventActionType
from apps.experiments.models import SourceMaterial, SyntheticVoice
from apps.service_providers.models import LlmProvider, LlmProviderModel, VoiceProvider


def _as_int(value) -> int | None:
    """Coerce a (possibly malformed) node-param id to ``int``, or ``None`` if it can't be."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_custom_actions(value) -> list[tuple[int, list[str]]]:
    """``custom_actions`` values are ``"{action_id}:{operation_id}"`` strings. Group the selected
    operation ids per custom action, preserving first-seen order."""
    selections: dict[int, list[str]] = {}
    for entry in value or []:
        action_part, _, operation_id = str(entry).partition(":")
        action_id = _as_int(action_part)
        if action_id is None:
            continue
        operation_ids = selections.setdefault(action_id, [])
        if operation_id and operation_id not in operation_ids:
            operation_ids.append(operation_id)
    return list(selections.items())


def iter_resource_refs(node_type: str, params: dict):
    """Yield ``(ResourceKind, raw_id)`` for every resource id a node of ``node_type`` references.

    The single id-collection traversal feeding ``ResourceFetcher``. Composites (``llm``),
    multi-source keys (``voice``) and list/parsed keys are handled per payload key; everything is
    driven by which keys the node type declares (``declared_resource_keys``)."""
    params = params or {}
    for key in declared_resource_keys(node_class_for(node_type)):
        rf = RESOURCE_FIELDS[key]
        if key == "llm":
            yield ResourceKind.LLM_PROVIDER, params.get("llm_provider_id")
            yield ResourceKind.LLM_PROVIDER_MODEL, params.get("llm_provider_model_id")
        elif key == "voice":
            yield ResourceKind.SYNTHETIC_VOICE, params.get("synthetic_voice_id")
        elif key == "custom_actions":
            for action_id, _operation_ids in parse_custom_actions(params.get("custom_actions")):
                yield ResourceKind.CUSTOM_ACTION, action_id
        elif rf.is_list:
            field = next(iter(rf.consumes))
            for raw_id in params.get(field) or []:
                yield rf.kind, raw_id
        else:
            field = next(iter(rf.consumes))
            yield rf.kind, params.get(field)


class ResourceFetcher:
    """Batch-loads team-scoped resources once, then serves them to the serializers as dict
    lookups. Built from the resolved target by the view and placed in serializer context."""

    def __init__(self, team):
        self.team = team
        self._objects: dict[ResourceKind, dict[int, object]] = {}
        self._pipelines: dict[int, object] = {}  # embedded pipeline_start pipelines, by id

    @classmethod
    def for_experiment(cls, experiment) -> "ResourceFetcher":
        fetcher = cls(experiment.team)
        ids: dict[ResourceKind, set[int]] = collections.defaultdict(set)

        if experiment.pipeline_id:
            for node in experiment.pipeline.node_set.all():
                fetcher._accumulate(node.type, node.params, ids)

        for trigger in (*experiment.static_triggers.all(), *experiment.timeout_triggers.all()):
            if trigger.is_archived:
                continue
            fetcher._collect_embedded_pipeline(trigger.action, ids)

        fetcher._load(ids)
        return fetcher

    def _collect_embedded_pipeline(self, action, ids: dict[ResourceKind, set[int]]) -> None:
        if action.action_type != EventActionType.PIPELINE_START:
            return
        pipeline_id = _as_int((action.params or {}).get("pipeline_id"))
        if not pipeline_id or pipeline_id in self._pipelines:
            return
        # Imported here to avoid an import cycle at module load (pipelines -> api -> pipelines).
        from apps.pipelines.models import Pipeline  # noqa: PLC0415

        pipeline = Pipeline.objects.filter(team=self.team, id=pipeline_id).prefetch_related("node_set").first()
        if pipeline is None:
            return
        self._pipelines[pipeline_id] = pipeline
        for node in pipeline.node_set.all():
            self._accumulate(node.type, node.params, ids)

    @staticmethod
    def _accumulate(node_type: str, params: dict, ids: dict[ResourceKind, set[int]]) -> None:
        for kind, raw_id in iter_resource_refs(node_type, params):
            rid = _as_int(raw_id)
            if rid is not None:
                ids[kind].add(rid)

    def _load(self, ids: dict[ResourceKind, set[int]]) -> None:
        for kind, id_set in ids.items():
            queryset = self._queryset(kind, id_set)
            self._objects[kind] = {obj.id: obj for obj in queryset} if queryset is not None else {}

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
            return LlmProviderModel.objects.filter(Q(team=team) | Q(team__isnull=True), id__in=ids)
        if kind == ResourceKind.VOICE_PROVIDER:
            return VoiceProvider.objects.filter(team=team, id__in=ids)
        if kind == ResourceKind.SYNTHETIC_VOICE:
            return SyntheticVoice.objects.filter(id__in=ids).select_related("voice_provider")
        assert_never(kind)

    def _get(self, kind: ResourceKind, raw_id) -> object | None:
        rid = _as_int(raw_id)
        if rid is None:
            return None
        return self._objects.get(kind, {}).get(rid)

    # ── accessors (dict lookups; an id not loaded -> None) ────────────────────────────────────────
    def llm_provider(self, raw_id):
        return self._get(ResourceKind.LLM_PROVIDER, raw_id)

    def llm_provider_model(self, raw_id):
        return self._get(ResourceKind.LLM_PROVIDER_MODEL, raw_id)

    def source_material(self, raw_id):
        return self._get(ResourceKind.SOURCE_MATERIAL, raw_id)

    def assistant(self, raw_id):
        return self._get(ResourceKind.ASSISTANT, raw_id)

    def custom_action(self, raw_id):
        return self._get(ResourceKind.CUSTOM_ACTION, raw_id)

    def collection(self, raw_id):
        return self._get(ResourceKind.COLLECTION, raw_id)

    def synthetic_voice(self, raw_id):
        return self._get(ResourceKind.SYNTHETIC_VOICE, raw_id)

    def voice_provider(self, raw_id):
        return self._get(ResourceKind.VOICE_PROVIDER, raw_id)

    def embedded_pipeline(self, raw_id):
        rid = _as_int(raw_id)
        return self._pipelines.get(rid) if rid is not None else None
