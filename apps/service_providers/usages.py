"""Find where service providers are being used.

Two public helpers:

* :func:`get_provider_usages` — given a provider instance, return all the
  objects that depend on it (pipelines/experiments via pipeline node params,
  plus anything connected by reverse FK such as assistants, analyses,
  collections, channels, etc.).
* :func:`search_providers_by_api_key` — given a :class:`ServiceProvider` type
  and an API key (or partial), iterate over providers of that type and
  return the ones whose encrypted config contains a matching secret.

Because provider ``config`` is encrypted at rest we cannot filter on the key
in SQL; we have to iterate and decrypt-on-access.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from apps.utils.deletion import get_related_objects

from .utils import ServiceProvider

MatchMode = Literal["exact", "suffix", "contains"]

_PIPELINE_PARAM_KEY_BY_PROVIDER_SLUG = {
    ServiceProvider.llm.slug: "llm_provider_id",
}

# Display overrides for category labels derived from ``verbose_name_plural``.
# "Experiment" is the internal model name; users know them as "Chatbots".
_CATEGORY_LABEL_OVERRIDES = {
    "experiments": "Chatbots",
}


def _category_label_for(model) -> str:
    plural = str(model._meta.verbose_name_plural)
    return _CATEGORY_LABEL_OVERRIDES.get(plural.lower(), plural.title())


@dataclass
class UsageCategory:
    label: str
    items: list = field(default_factory=list)
    # ``kind`` lets the template pick the right item layout:
    #   "list"  — items are model instances (default).
    #   "chatbots_with_pipelines" — items are ``{"chatbot": exp, "pipelines": [...]}`` dicts.
    #   "pipelines" — items are unlinked Pipeline instances.
    kind: str = "list"

    def __len__(self) -> int:
        return len(self.items)


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

    Walks the reverse-FK relations Django would follow on delete, and — for
    LLM providers — additionally walks pipeline ``Node.params`` that
    reference the provider by id, then collects the chatbots (Experiments)
    whose pipeline contains those nodes.
    """
    service_provider = _service_provider_for(provider)
    pipeline_param_key = _PIPELINE_PARAM_KEY_BY_PROVIDER_SLUG.get(service_provider.slug)
    related = get_related_objects(provider, pipeline_param_key=pipeline_param_key)

    grouped: dict[str, list] = defaultdict(list)
    pipelines: list = []
    channels: list = []
    for obj in related:
        model = obj.__class__
        if model.__name__ == "Pipeline":
            pipelines.append(obj)
        elif model.__name__ == "ExperimentChannel":
            channels.append(obj)
        else:
            grouped[_category_label_for(model)].append(obj)

    categories = [UsageCategory(label=label, items=items) for label, items in sorted(grouped.items())]

    if pipelines:
        categories.extend(_build_pipeline_categories(pipelines))
    if channels:
        categories.extend(_build_channel_categories(channels))

    return ProviderUsages(provider=provider, categories=categories)


def _build_pipeline_categories(pipelines: list) -> list[UsageCategory]:
    """Return up to two categories built from pipeline references.

    Chatbots are the primary grouping: each entry lists the pipelines that
    a single chatbot routes through. Pipelines with no chatbot reaching
    them appear in a separate "Unlinked Pipelines" category so they're
    still discoverable. A chatbot may reach a pipeline directly
    (``Experiment.pipeline``) or via an event-configuration
    ``pipeline_start`` action.
    """
    unique_pipelines = _dedupe_by_id(pipelines)
    pipeline_ids = {p.id for p in unique_pipelines}
    experiments_by_pipeline = _experiments_for_pipelines(pipeline_ids)

    chatbots: dict[int, dict] = {}
    unlinked: list = []
    for pipeline in unique_pipelines:
        experiments = experiments_by_pipeline.get(pipeline.id, [])
        if not experiments:
            unlinked.append(pipeline)
            continue
        for exp in experiments:
            entry = chatbots.setdefault(exp.id, {"chatbot": exp, "pipelines": []})
            if all(p.id != pipeline.id for p in entry["pipelines"]):
                entry["pipelines"].append(pipeline)

    categories: list[UsageCategory] = []
    if chatbots:
        categories.append(
            UsageCategory(label="Chatbots", kind="chatbots_with_pipelines", items=list(chatbots.values()))
        )
    if unlinked:
        categories.append(UsageCategory(label="Unlinked Pipelines", kind="pipelines", items=unlinked))
    return categories


def _build_channel_categories(channels: list) -> list[UsageCategory]:
    """Return up to two categories built from ExperimentChannel references.

    Channels are owned by chatbots (``ExperimentChannel.experiment``), so we
    roll up to chatbots — the same shape the LLM page uses for pipelines —
    and leave any unowned channels in an "Unlinked Channels" bucket.
    """
    from apps.experiments.models import Experiment  # noqa: PLC0415 — app-import cycle

    unique_channels = _dedupe_by_id(channels)
    experiment_ids = {ch.experiment_id for ch in unique_channels if ch.experiment_id}
    experiments_by_id = (
        {exp.id: exp for exp in Experiment.objects.filter(id__in=experiment_ids).select_related("team")}
        if experiment_ids
        else {}
    )

    chatbots: dict[int, dict] = {}
    unlinked: list = []
    for channel in unique_channels:
        exp = experiments_by_id.get(channel.experiment_id) if channel.experiment_id else None
        if exp is None:
            unlinked.append(channel)
            continue
        entry = chatbots.setdefault(exp.id, {"chatbot": exp, "channels": []})
        entry["channels"].append(channel)

    categories: list[UsageCategory] = []
    if chatbots:
        categories.append(UsageCategory(label="Chatbots", kind="chatbots_with_channels", items=list(chatbots.values())))
    if unlinked:
        categories.append(UsageCategory(label="Unlinked Channels", kind="list", items=unlinked))
    return categories


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
    from apps.events.models import EventActionType, StaticTrigger, TimeoutTrigger  # noqa: PLC0415 — app cycle
    from apps.experiments.models import Experiment  # noqa: PLC0415 — app cycle

    by_pipeline: dict[int, dict[int, object]] = defaultdict(dict)
    for exp in Experiment.objects.filter(pipeline_id__in=pipeline_ids).select_related("team"):
        by_pipeline[exp.pipeline_id][exp.id] = exp

    # Indirect link: an EventAction of type "pipeline_start" stores the
    # pipeline id in ``params["pipeline_id"]`` (stored as int or str).
    pipeline_id_values: list = [*pipeline_ids, *(str(pid) for pid in pipeline_ids)]
    trigger_filter = {
        "action__action_type": EventActionType.PIPELINE_START,
        "action__params__pipeline_id__in": pipeline_id_values,
    }
    for trigger_qs in (
        StaticTrigger.objects.filter(**trigger_filter).select_related("action", "experiment", "experiment__team"),
        TimeoutTrigger.objects.filter(**trigger_filter).select_related("action", "experiment", "experiment__team"),
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
        secret_fields = _secret_field_names(provider)
        if not secret_fields:
            continue
        if any(_value_matches(provider.config.get(f, ""), key, match) for f in secret_fields):
            matches.append(provider)
    return matches


def _secret_field_names(provider) -> Iterable[str]:
    """The set of config keys that hold credentials for ``provider``'s subtype."""
    form_cls = getattr(provider.type_enum, "form_cls", None)
    if form_cls is None:
        return ()
    return getattr(form_cls, "obfuscate_fields", ()) or ()


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
