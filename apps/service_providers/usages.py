"""Find where service providers are being used.

Two public helpers:

* :func:`get_provider_usages` — given a provider instance, return all the
  objects that depend on it (pipelines/experiments via pipeline node params,
  plus anything connected by reverse FK such as analyses, collections,
  channels, etc.).
* :func:`search_providers_by_api_key` — given a :class:`ServiceProvider` type
  and an API key (or partial), iterate over providers of that type and
  return the ones whose encrypted config contains a matching secret.

Because provider ``config`` is encrypted at rest we cannot filter on the key
in SQL; we have to iterate and decrypt-on-access.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal

from apps.documents.models import Collection
from apps.events.models import EventActionType, StaticTrigger, TimeoutTrigger
from apps.experiments.models import Experiment
from apps.utils.deletion import get_related_objects

from .utils import ServiceProvider

logger = logging.getLogger(__name__)

MatchMode = Literal["exact", "suffix", "contains"]

_PARAM_KEY_BY_PROVIDER_SLUG = {
    ServiceProvider.llm.slug: "llm_provider_id",
}

# Reverse-FK related models that aren't useful to surface on the usages page.
# SyntheticVoice rows are managed inside the voice provider's own edit view,
# so showing them on the usages page just adds noise.
_EXCLUDED_REFERENCE_MODELS = {"SyntheticVoice"}

# Version pills rendered inline on a row before the remainder collapse behind a
# "+N more" toggle. A busy chatbot can carry 25+ referencing versions; showing them
# all inline turns one row into a paragraph.
MAX_INLINE_VERSIONS = 8


@dataclass
class UsageGroup:
    """One version family, reported as a single row.

    ``head`` supplies the row's name and primary link. It is always the working
    version — that is what owns the edit UI — even when the working version does
    not itself reference the provider. ``versions`` holds the family members that
    *do* reference it, working version first then ascending version number.

    Unversioned objects are their own single-member family, so callers can treat
    every row the same way.
    """

    head: object
    versions: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return getattr(self.head, "name", None) or str(self.head)

    @property
    def visible_versions(self) -> list:
        return self.versions[:MAX_INLINE_VERSIONS]

    @property
    def hidden_versions(self) -> list:
        return self.versions[MAX_INLINE_VERSIONS:]


@dataclass
class UsageCategory:
    label: str
    items: list = field(default_factory=list)

    @cached_property
    def groups(self) -> list[UsageGroup]:
        """``items`` collapsed to one entry per version family — what the page renders."""
        return _group_by_version_family(self.items)

    def __len__(self) -> int:
        """The number of rows shown, i.e. version families rather than raw objects."""
        return len(self.groups)


@dataclass
class ProviderUsages:
    provider: object
    categories: list[UsageCategory] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(c) for c in self.categories)

    def is_empty(self) -> bool:
        return self.total == 0


def get_provider_usages(provider) -> ProviderUsages:
    """Return all objects that depend on ``provider``.

    Chatbots reached either directly (reverse FK to Experiment) or
    indirectly (via pipelines or ExperimentChannels) are merged into a
    single deduped "Chatbots" category. Pipelines or channels that aren't
    reached by any chatbot stay visible in "Unlinked Pipelines" /
    "Unlinked Channels" categories. Document sources roll up to their
    parent Collection.

    Pipeline nodes reference providers from a JSON ``params`` blob rather than a
    foreign key, so they are looked up by param key. Everything else, evaluators
    included, is reached through a reverse FK.

    Each category keeps every referencing object in ``items`` but reports rows
    through ``groups``, which collapses version families to one row apiece — so
    ``total`` counts families, matching what the page shows.
    """
    service_provider = _service_provider_for(provider)
    params_key = _PARAM_KEY_BY_PROVIDER_SLUG.get(service_provider.slug)
    related = get_related_objects(provider, pipeline_param_key=params_key)

    # Per-category dicts dedupe rows that are reachable through more than one
    # reverse relation (e.g. TranscriptAnalysis has both llm_provider and
    # translation_llm_provider FKs to LlmProvider).
    other_grouped: dict[str, dict[int, object]] = defaultdict(dict)
    pipelines: list = []
    channels: list = []
    document_sources: list = []
    chatbots: dict[int, Experiment] = {}

    for obj in related:
        model = obj.__class__
        if model.__name__ in _EXCLUDED_REFERENCE_MODELS:
            continue
        if model.__name__ == "Experiment":
            chatbots[obj.id] = obj
        elif model.__name__ == "Pipeline":
            pipelines.append(obj)
        elif model.__name__ == "ExperimentChannel":
            channels.append(obj)
        elif model.__name__ == "DocumentSource":
            document_sources.append(obj)
        else:
            other_grouped[str(model._meta.verbose_name_plural).title()][obj.pk] = obj

    trailing_categories: list[UsageCategory] = []
    if pipelines:
        chatbots_from_pipelines, unlinked_pipelines = _resolve_pipeline_chatbots(pipelines)
        chatbots.update({exp.id: exp for exp in chatbots_from_pipelines})
        if unlinked_pipelines:
            trailing_categories.append(
                UsageCategory(label="Unlinked Pipelines", items=sorted(unlinked_pipelines, key=_display_key))
            )
    if channels:
        chatbots_from_channels, unlinked_channels = _resolve_channel_chatbots(channels)
        chatbots.update({exp.id: exp for exp in chatbots_from_channels})
        if unlinked_channels:
            trailing_categories.append(
                UsageCategory(label="Unlinked Channels", items=sorted(unlinked_channels, key=_display_key))
            )
    if document_sources:
        trailing_categories.extend(_build_document_source_categories(document_sources))

    categories: list[UsageCategory] = []
    if chatbots:
        categories.append(UsageCategory(label="Chatbots", items=sorted(chatbots.values(), key=_display_key)))
    for label in sorted(other_grouped):
        categories.append(UsageCategory(label=label, items=sorted(other_grouped[label].values(), key=_display_key)))
    categories.extend(trailing_categories)

    return ProviderUsages(provider=provider, categories=categories)


def _display_key(obj) -> tuple[str, int]:
    """Sort key for usage items: case-insensitive name, then pk for stability."""
    name = getattr(obj, "name", None) or str(obj)
    return (name.lower(), obj.pk)


def _group_by_version_family(items: list) -> list[UsageGroup]:
    """Collapse ``items`` so each version family occupies one group.

    Keyed on (model, working version id) so unrelated models that happen to share
    a pk never merge. Objects with no ``working_version_id`` are their own family,
    which covers both unversioned models and working versions.
    """
    families: dict[tuple[str, int], list] = defaultdict(list)
    for obj in items:
        families[(obj.__class__.__name__, _family_id(obj))].append(obj)

    groups = [
        UsageGroup(head=_family_head(members), versions=sorted(members, key=_version_sort_key))
        for members in families.values()
    ]
    return sorted(groups, key=lambda group: _display_key(group.head))


def _family_id(obj) -> int:
    return getattr(obj, "working_version_id", None) or obj.pk


def _family_head(members: list):
    """The family's working version, preferring one that is already in ``members``."""
    for member in members:
        if getattr(member, "working_version_id", None) is None:
            return member
    # Only snapshots reference the provider. Every queryset feeding a category
    # select_relates ``working_version`` (``_with_working_version`` for reverse-FK
    # items, explicit ``select_related`` in the pipeline/channel/collection lookups),
    # so this stays query-free. Any new source must do the same.
    return members[0].get_working_version()


def _version_sort_key(obj) -> tuple[int, int]:
    """Working version first, then ascending version number."""
    if getattr(obj, "working_version_id", None) is None:
        return (0, 0)
    return (1, getattr(obj, "version_number", None) or 0)


def _resolve_pipeline_chatbots(pipelines: list) -> tuple[list[Experiment], list]:
    """Return chatbots reachable via the given pipelines, and any unreached pipelines.

    A chatbot may reach a pipeline directly (``Experiment.pipeline``) or
    indirectly through a ``pipeline_start`` event action configured on a
    StaticTrigger / TimeoutTrigger.
    """
    unique_pipelines = _dedupe_by_id(pipelines)
    pipeline_ids = {p.id for p in unique_pipelines}
    experiments_by_pipeline = _experiments_for_pipelines(pipeline_ids)

    chatbots: dict[int, Experiment] = {}
    unlinked: list = []
    for pipeline in unique_pipelines:
        experiments = experiments_by_pipeline.get(pipeline.id, [])
        if not experiments:
            unlinked.append(pipeline)
            continue
        for exp in experiments:
            chatbots[exp.id] = exp
    return list(chatbots.values()), unlinked


def _resolve_channel_chatbots(channels: list) -> tuple[list[Experiment], list]:
    """Return chatbots that own the given channels, and any orphaned channels."""
    unique_channels = _dedupe_by_id(channels)
    experiment_ids = {ch.experiment_id for ch in unique_channels if ch.experiment_id}
    experiments_by_id = (
        {
            exp.id: exp
            for exp in Experiment.objects.filter(id__in=experiment_ids).select_related("team", "working_version")
        }
        if experiment_ids
        else {}
    )

    chatbots: dict[int, Experiment] = {}
    unlinked: list = []
    for channel in unique_channels:
        exp = experiments_by_id.get(channel.experiment_id) if channel.experiment_id else None
        if exp is None:
            unlinked.append(channel)
            continue
        chatbots[exp.id] = exp
    return list(chatbots.values()), unlinked


def _build_document_source_categories(document_sources: list) -> list[UsageCategory]:
    """Roll up document sources to the Collection they belong to.

    Document sources don't have their own user-facing page; users find them
    inside the parent collection's edit view. So we report each unique
    collection once, with the collection's own version status surfaced via
    the shared item partial.
    """
    collection_ids = {ds.collection_id for ds in document_sources if ds.collection_id}
    if not collection_ids:
        return []
    collections = list(
        Collection.objects.filter(id__in=collection_ids).select_related("team", "working_version").order_by("name")
    )
    return [UsageCategory(label="Collections", items=collections)]


def _dedupe_by_id(items: list) -> list:
    seen: set[int] = set()
    result = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        result.append(item)
    return result


def _experiments_for_pipelines(pipeline_ids: set[int]) -> dict[int, list]:
    by_pipeline: dict[int, dict[int, object]] = defaultdict(dict)
    for exp in Experiment.objects.filter(pipeline_id__in=pipeline_ids).select_related("team", "working_version"):
        by_pipeline[exp.pipeline_id][exp.id] = exp

    # Indirect link: an EventAction of type "pipeline_start" stores the
    # pipeline id in ``params["pipeline_id"]`` (stored as int or str).
    pipeline_id_values: list = [*pipeline_ids, *(str(pid) for pid in pipeline_ids)]
    trigger_filter = {
        "action__action_type": EventActionType.PIPELINE_START,
        "action__params__pipeline_id__in": pipeline_id_values,
    }
    trigger_select_related = ("action", "experiment", "experiment__team", "experiment__working_version")
    for trigger_qs in (
        StaticTrigger.objects.filter(**trigger_filter).select_related(*trigger_select_related),
        TimeoutTrigger.objects.filter(**trigger_filter).select_related(*trigger_select_related),
    ):
        for trigger in trigger_qs:
            raw_pipeline_id = trigger.action.params.get("pipeline_id")
            try:
                pid = int(raw_pipeline_id)
            except (TypeError, ValueError):
                continue
            if pid not in pipeline_ids:
                continue
            by_pipeline[pid][trigger.experiment_id] = trigger.experiment

    return {pid: list(exps.values()) for pid, exps in by_pipeline.items()}


def _service_provider_for(provider) -> ServiceProvider:
    for member in ServiceProvider:
        if isinstance(provider, member.model):
            return member
    raise ValueError(f"No ServiceProvider entry for {type(provider).__name__}")


def search_providers_by_api_key(
    service_provider: ServiceProvider,
    key: str,
    match: MatchMode = "exact",
    *,
    subtype_slug: str | None = None,
) -> list:
    """Return providers of ``service_provider`` type whose secret matches ``key``.

    Args:
        service_provider: which provider table to search.
        key: the API key (or substring/suffix) to look for. Empty raises.
        match: one of ``"exact"``, ``"suffix"``, ``"contains"``.
        subtype_slug: if given, restrict to providers of this subtype
            (e.g. ``"anthropic"`` for LLM providers).
    """
    if not key:
        raise ValueError("key must be a non-empty string")

    queryset = service_provider.model.objects.select_related("team")
    if subtype_slug:
        queryset = queryset.filter(type=subtype_slug)

    matches = []
    for provider in queryset.iterator():
        fields = _searchable_field_names(provider)
        if not fields:
            continue
        if any(_value_matches(provider.config.get(f, ""), key, match) for f in fields):
            matches.append(provider)
    return matches


def _searchable_field_names(provider) -> Iterable[str]:
    """Config keys that can be matched against a supplied key for ``provider``'s subtype.

    Includes the obfuscated secret fields plus any non-secret public identifiers
    that the form opts into via ``additional_searchable_fields`` (e.g. Langfuse's
    ``public_key`` or AWS' ``aws_access_key_id``).
    """
    try:
        type_enum = provider.type_enum
    except (KeyError, ValueError):
        logger.warning(
            "Skipping provider %s (id=%s) with unrecognised type %r",
            type(provider).__name__,
            provider.pk,
            provider.type,
        )
        return ()
    form_cls = getattr(type_enum, "form_cls", None)
    if form_cls is None:
        return ()
    obfuscated = getattr(form_cls, "obfuscate_fields", ()) or ()
    public = getattr(form_cls, "additional_searchable_fields", ()) or ()
    return [*obfuscated, *public]


def _value_matches(stored: object, target: str, match: MatchMode) -> bool:
    if not isinstance(stored, str) or not stored:
        return False
    if match == "exact":
        return stored == target
    if match == "suffix":
        return stored.endswith(target)
    if match == "contains":
        return target in stored
    raise ValueError(f"unknown match mode: {match!r}")
