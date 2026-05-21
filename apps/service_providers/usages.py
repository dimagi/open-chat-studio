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


@dataclass
class UsageCategory:
    label: str
    items: list = field(default_factory=list)

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
    for obj in related:
        model = obj.__class__
        if model.__name__ == "Pipeline":
            pipelines.append(obj)
            continue
        grouped[model._meta.verbose_name_plural.title()].append(obj)

    categories = [UsageCategory(label=label, items=items) for label, items in sorted(grouped.items())]

    if pipelines:
        categories.append(_build_pipeline_category(pipelines))

    return ProviderUsages(provider=provider, categories=categories)


def _build_pipeline_category(pipelines: list) -> UsageCategory:
    """Group pipelines together with the Experiments that reference them."""
    from apps.experiments.models import Experiment  # noqa: PLC0415 — avoids app-import cycle

    pipeline_ids = {p.id for p in pipelines}
    experiments_by_pipeline: dict[int, list] = defaultdict(list)
    for exp in Experiment.objects.filter(pipeline_id__in=pipeline_ids).select_related("team"):
        experiments_by_pipeline[exp.pipeline_id].append(exp)

    items = []
    seen: set[int] = set()
    for pipeline in pipelines:
        if pipeline.id in seen:
            continue
        seen.add(pipeline.id)
        items.append({"pipeline": pipeline, "experiments": experiments_by_pipeline.get(pipeline.id, [])})
    return UsageCategory(label="Pipelines", items=items)


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
