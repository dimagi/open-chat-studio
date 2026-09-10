#!/usr/bin/env python3
"""
Reconcile OCS's in-repo model catalogue and pricing seed against LiteLLM's
``model_prices_and_context_window.json``, which is the single upstream source:
it carries pricing, token limits, provider, model kind and deprecation dates
for ~3 900 models, is updated several times a day, and needs no credentials.

Discovery works by diffing the file against its own git history: a key present
today and absent in the commit from ``--baseline-days`` ago is newly published.

One daily run produces four signals consumed by the ``auto-update-models``
workflow:

* **new_models** - models upstream that OCS hasn't registered yet. Selected by
  state rather than by a date window, so a day the workflow was broken heals on
  the next run; recently-published ones are offered first, capped per run, with
  the remainder reported as ``backlog``. Feeds the Claude Code job that opens a
  "Register new models" PR. Models a reviewer looked at and rejected are
  recorded in ``scripts/reconcile_ignored_models.json`` so they stop occupying
  a slot ahead of the backlog.
* **price_changes** - existing seed entries whose upstream rate has moved.
  Rewrites ``llm_pricing.json`` in place and emits a
  ``NNNN_rate_update_YYYYMMDD.py`` data migration, so the workflow can open a
  mechanical "Pricing update" PR.
* **missing_pricing** - models in ``default_models.py`` with no usable seed
  entry (no ``llm_input``/``llm_output`` rule). Feeds a "missing pricing"
  GitHub issue so OCS-managed coverage gaps surface as a tracked task.
* **deprecated_upstream** - models OCS still lists as active whose upstream
  deprecation date has passed. Feeds the same Claude Code job, which marks them
  ``deprecated=True``.

Usage (from the repo root)::

    python scripts/reconcile_models.py \\
        [--baseline-days 7] \\
        [--repo-root .] \\
        [--output reconciliation.json] \\
        [--dry-run] \\
        [--today YYYY-MM-DD]    # deterministic-tests override

Side effects (only when ``price_changes`` is non-empty, and never under
``--dry-run``):

* Overwrites ``apps/cost_tracking/seed_data/llm_pricing.json`` with the new
  rates.
* Writes a new migration at
  ``apps/cost_tracking/migrations/NNNN_rate_update_YYYYMMDD.py``.

Side effects (always, when running under GitHub Actions):

* Appends gate variables to ``$GITHUB_OUTPUT``::

    has_new_models, new_model_count, new_model_ids
    has_price_changes, price_change_count, backfilled_count,
        pricing_pr_title, pricing_pr_body_path
    has_missing_pricing, missing_pricing_count, missing_pricing_issue_body_path
    has_deprecated_upstream, deprecated_upstream_count

Exit code is 1 when the price table can't be read; every signal derives from
it, so a run without it has no output to give.

Pricing unit conventions
------------------------
* OCS ``llm_pricing.json`` stores ``unit_price`` per **1 000** tokens.
* LiteLLM's ``model_prices_and_context_window.json`` is per **1** token
  (multiply by 1 000).
"""

from __future__ import annotations

import argparse
import datetime
import enum
import io
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from reconcile_catalogue import (
    load_active_default_models,
    load_ignored_models,
    load_registered_models,
)
from reconcile_http import _get_json, _github_headers

# Constants


class _ExtraSegments(enum.Enum):
    """What a key's segments after the provider namespace mean."""

    # A preset, or a pass-through to another vendor. Not a model this provider
    # serves under this name (``perplexity/openai/gpt-5.1``).
    REJECT = "reject"
    # A deployment region or tier, not part of the model name
    # (``azure/eu/gpt-4o``).
    REGION = "region"
    # The originating vendor, which this provider's API expects as part of the
    # model ID (``groq/openai/gpt-oss-120b``).
    MODEL_NAME = "model_name"


@dataclass(frozen=True)
class _ProviderNamespace:
    """How LiteLLM keys one provider's models, and what OCS calls that provider."""

    ocs_provider: str
    prefix: str
    extra_segments: _ExtraSegments = _ExtraSegments.REJECT


# LiteLLM tags every entry with the provider that serves it, so which OCS
# providers offer a model is read from the data rather than guessed from the
# organisation that published it. ``prefix`` is the key namespace that provider
# uses, which is not always its tag: Google's AI Studio models are tagged
# "gemini" and keyed "gemini/...", while Vertex models are tagged
# "vertex_ai-language-models" and keyed "vertex_ai/..." or bare.
LITELLM_PROVIDERS: dict[str, _ProviderNamespace] = {
    "openai": _ProviderNamespace("openai", "openai"),
    "azure": _ProviderNamespace("azure", "azure", _ExtraSegments.REGION),
    "anthropic": _ProviderNamespace("anthropic", "anthropic"),
    "gemini": _ProviderNamespace("google", "gemini"),
    # Vertex tags its gemini models two ways and keys some of them both bare
    # and namespaced, so both tags map to the same OCS provider.
    "vertex_ai-language-models": _ProviderNamespace("google_vertex_ai", "vertex_ai"),
    "vertex_ai": _ProviderNamespace("google_vertex_ai", "vertex_ai"),
    "deepseek": _ProviderNamespace("deepseek", "deepseek"),
    "perplexity": _ProviderNamespace("perplexity", "perplexity"),
    "groq": _ProviderNamespace("groq", "groq", _ExtraSegments.MODEL_NAME),
    "minimax": _ProviderNamespace("minimax", "minimax"),
}

OCS_PROVIDER_PREFIX: dict[str, str] = {ns.ocs_provider: ns.prefix for ns in LITELLM_PROVIDERS.values()}

# The kinds of model OCS can actually run as a chatbot. "responses" is the
# OpenAI Responses API, which covers four models OCS already registers.
CHAT_MODES = frozenset({"chat", "responses"})

# Some entries are tagged mode "chat" but only emit audio (Google's lyria music
# models). Where the field is present it is the more reliable signal.
TEXT_MODALITY = "text"

# Seed providers whose rates are diffed against LiteLLM. Deliberately narrower
# than the seed: these are the upstreams whose published rates LiteLLM mirrors
# closely enough to rewrite the seed from. Widening it would put groq/deepseek
# rates under automated rewrite too, which is a separate decision.
DIFFABLE_PROVIDERS = frozenset({"openai", "azure", "anthropic", "google", "google_vertex_ai"})

# A model with neither llm_input nor llm_output seed pricing is treated as
# missing, regardless of whether a cached-input rate exists.
REQUIRED_SERVICE_KINDS = frozenset({"llm_input", "llm_output"})

LITELLM_REPO = "BerriAI/litellm"
LITELLM_PRICING_PATH = "model_prices_and_context_window.json"
LITELLM_PRICING_URL = f"https://raw.githubusercontent.com/{LITELLM_REPO}/refs/heads/main/{LITELLM_PRICING_PATH}"
LITELLM_PRICING_AT_URL = f"https://raw.githubusercontent.com/{LITELLM_REPO}/{{sha}}/{LITELLM_PRICING_PATH}"
LITELLM_SOURCE_URL = f"https://github.com/{LITELLM_REPO}/blob/main/{LITELLM_PRICING_PATH}"
LITELLM_COMMITS_URL = (
    f"https://api.github.com/repos/{LITELLM_REPO}/commits?path={LITELLM_PRICING_PATH}&until={{until}}&per_page=1"
)

# How far back to look for "newly published". Only affects ordering: an older
# model OCS never registered is still a candidate, just behind the recent ones.
DEFAULT_BASELINE_DAYS = 7

# Registering everything unregistered at once would be an unreviewable PR, so
# each run takes the newest slice and leaves the rest for the next one. Kept
# small because each candidate is a judgement call for a human to check.
MAX_NEW_MODELS_PER_RUN = 10

LLM_PRICING_REL_PATH = "apps/cost_tracking/seed_data/llm_pricing.json"
MIGRATIONS_DIR_REL_PATH = "apps/cost_tracking/migrations"

NO_PRICING_REASON = "No pricing data found in the LiteLLM price table"

# Seed I/O


def load_seed(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def seed_index(seed: list[dict]) -> dict[tuple[str, str], dict[str, str]]:
    """``{(provider_type, model_name): {service_kind: unit_price}}``."""
    return {
        (entry["provider_type"], entry["model_name"]): {r["service_kind"]: r["unit_price"] for r in entry["rules"]}
        for entry in seed
    }


# Pricing resolvers


def _fmt(value: float | None) -> str | None:
    """Up to 8 significant digits, trailing zeros stripped. None -> None."""
    if value is None:
        return None
    return str(Decimal(f"{value:.8g}").normalize())


def _per_token_to_per_1k(v: float | None) -> float | None:
    return v * 1_000.0 if v is not None else None


def _rates_from_raw(
    raw: dict[str, float | None],
    convert: Callable[[float | None], float | None],
) -> dict[str, str] | None:
    """Convert raw prices to per-1K strings. Returns ``None`` when both
    input and output are absent (cached-input-only isn't useful on its own).
    """
    if raw.get("llm_input") is None and raw.get("llm_output") is None:
        return None
    result: dict[str, str] = {}
    for kind, value in raw.items():
        price = _fmt(convert(value))
        if price is not None:
            result[kind] = price
    return result or None


def _litellm_entry(model_id: str, litellm_data: dict[str, Any], provider: str | None = None) -> dict | None:
    """Find a model's entry in the price table.

    OCS stores bare model names; LiteLLM namespaces many keys by the provider
    that serves them, and the two names for a provider differ (OCS "google" is
    LiteLLM "gemini"). Namespaced keys are tried before the bare key, because a
    reseller charges its own rate for a model it did not originate and LiteLLM's
    bare key holds the originating vendor's rate. A lookup with no provider is
    restricted to the bare key, which keeps it off any reseller's rate.
    """
    keys = []
    if provider:
        prefix = OCS_PROVIDER_PREFIX.get(provider, provider)
        # Upstream self-namespaces some keys ("perplexity/perplexity/sonar"),
        # so both depths are probed to reach the row discovery records.
        keys += [f"{prefix}/{model_id}", f"{prefix}/{prefix}/{model_id}"]
    keys.append(model_id)
    for key in keys:
        entry = litellm_data.get(key)
        if isinstance(entry, dict):
            return entry
    return None


def resolve_pricing_from_litellm(
    model_id: str,
    litellm_data: dict[str, Any],
    provider: str | None = None,
) -> dict[str, str] | None:
    """Per-1K rates for a model from the LiteLLM price table."""
    entry = _litellm_entry(model_id, litellm_data, provider)
    if entry is None:
        return None
    return _rates_from_raw(
        {
            "llm_input": entry.get("input_cost_per_token"),
            "llm_output": entry.get("output_cost_per_token"),
            "llm_cached_input": entry.get("cache_read_input_token_cost"),
            "llm_cache_write": entry.get("cache_creation_input_token_cost"),
        },
        _per_token_to_per_1k,
    )


def build_pricing_entries(model_id: str, rates_by_provider: dict[str, dict[str, str]]) -> list[dict]:
    """One seed entry per provider, each carrying that provider's own rates."""
    return [
        {
            "provider_type": provider,
            "model_name": model_id,
            "rules": [{"service_kind": kind, "unit_price": price} for kind, price in rates.items()],
        }
        for provider, rates in rates_by_provider.items()
        if rates
    ]


# Discovery via the price table's own git history


def _litellm_at(sha: str) -> dict[str, Any]:
    return _get_json(LITELLM_PRICING_AT_URL.format(sha=sha))


def baseline_sha(days: int, today: datetime.date | None = None) -> str | None:
    """SHA of the last commit to touch the price table before *days* ago.

    Returns None when GitHub can't be reached; recency is then unknown and the
    run continues rather than failing.
    """
    cutoff = (today or datetime.datetime.now(datetime.UTC).date()) - datetime.timedelta(days=days)
    try:
        commits = _get_json(
            LITELLM_COMMITS_URL.format(until=f"{cutoff.isoformat()}T00:00:00Z"), headers=_github_headers()
        )
    except Exception as exc:  # ordering is a nicety, not worth failing over
        print(f"  (!) could not read LiteLLM history ({exc}); recency unknown.")
        return None
    return commits[0]["sha"] if commits else None


def fetch_baseline(days: int, today: datetime.date | None = None) -> dict[str, Any] | None:
    """The price table as it stood *days* ago, or None when it can't be read.

    None and an empty table are different answers: an unread baseline makes
    every model look new, so callers must be able to tell them apart.
    """
    sha = baseline_sha(days, today)
    if not sha:
        return None
    try:
        return _litellm_at(sha) or None
    except Exception as exc:
        print(f"  (!) could not read the baseline price table ({exc}); recency unknown.")
        return None


def _key_for_provider(key: str, litellm_provider: str | None) -> tuple[str, str] | None:
    """Map a price-table key to ``(ocs_provider, model_name)``, or None to skip it.

    LiteLLM reuses the segments after its provider namespace for three
    different things, and only ``_ExtraSegments`` says which: an Azure region
    or tier, a Perplexity preset or pass-through to another vendor, or - for
    Groq alone - part of the model ID its API expects.
    """
    namespace = LITELLM_PROVIDERS.get(litellm_provider)
    if namespace is None:
        return None
    name = key
    prefix = f"{namespace.prefix}/"
    while name.startswith(prefix):
        name = name.removeprefix(prefix)
    if not name:
        return None
    if "/" in name:
        if namespace.extra_segments is _ExtraSegments.REJECT:
            return None
        if namespace.extra_segments is _ExtraSegments.REGION:
            name = name.rsplit("/", 1)[-1]
    return namespace.ocs_provider, name


def _model_names(litellm_data: dict[str, Any]) -> set[str]:
    """Every OCS-shaped model name in a price table, however its keys are namespaced."""
    names = set()
    for key, entry in litellm_data.items():
        if not isinstance(entry, dict):
            continue
        mapped = _key_for_provider(key, entry.get("litellm_provider"))
        if mapped:
            names.add(mapped[1])
    return names


def _serves_chat(entry: Any) -> bool:
    """A price-table entry OCS could run as a chatbot."""
    if not isinstance(entry, dict) or entry.get("mode") not in CHAT_MODES:
        return False
    modalities = entry.get("supported_output_modalities")
    return TEXT_MODALITY in modalities if isinstance(modalities, list) else True


def _is_deprecated(entry: dict, today: datetime.date) -> bool:
    raw = entry.get("deprecation_date")
    if not raw:
        return False
    try:
        return datetime.date.fromisoformat(str(raw)[:10]) <= today
    except ValueError:
        return False


def eligible_models(litellm_data: dict[str, Any], today: datetime.date | None = None) -> dict[str, dict]:
    """Chat-capable models from providers OCS supports, keyed by model name.

    One model can be served by several providers (``gpt-4o`` on both openai and
    azure), so entries are merged: which providers offer it comes from the data
    rather than from a hardcoded org mapping.
    """
    today = today or datetime.datetime.now(datetime.UTC).date()
    merged: dict[str, dict] = {}
    for key, entry in litellm_data.items():
        if not _serves_chat(entry) or _is_deprecated(entry, today):
            continue
        mapped = _key_for_provider(key, entry.get("litellm_provider"))
        if mapped is None:
            continue
        ocs_provider, name = mapped
        record = merged.setdefault(name, {"id": name, "providers": [], "keys": {}, "deprecation_date": None})
        _merge_provider_key(record, key, entry, ocs_provider)
    for record in merged.values():
        record["providers"].sort()
    return merged


def _merge_provider_key(record: dict, key: str, entry: dict, ocs_provider: str) -> None:
    """Fold one price-table key into the record for the model name it maps to."""
    if ocs_provider not in record["providers"]:
        record["providers"].append(ocs_provider)
    if _prefers_key(key, record["keys"].get(ocs_provider), ocs_provider):
        record["keys"][ocs_provider] = key
    record["deprecation_date"] = record["deprecation_date"] or entry.get("deprecation_date")


def _prefers_key(key: str, incumbent: str | None, ocs_provider: str) -> bool:
    return incumbent is None or _key_rank(key, ocs_provider) < _key_rank(incumbent, ocs_provider)


def _key_rank(key: str, ocs_provider: str) -> tuple[int, int]:
    """Lower is better. A namespaced key beats a bare one so pricing reads the
    reseller's own rate, and a plain one beats a regional variant of the same
    model so the seed doesn't inherit a region's premium.
    """
    prefix = f"{OCS_PROVIDER_PREFIX[ocs_provider]}/"
    return (0 if key.startswith(prefix) else 1, key.count("/"))


def select_candidates(
    litellm_data: dict[str, Any],
    baseline: dict[str, Any] | None,
    registered: dict[str, set[str]],
    ignored: dict[str, set[str]] | None = None,
    today: datetime.date | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split unregistered models into this run's candidates and the backlog.

    Selection is by state, not by a date window, so nothing is lost when a run
    fails. Models that appeared in the price table since the baseline are
    offered first, because those are the ones a reviewer is expecting. A
    *baseline* of None means the history was unreachable, so recency is
    reported as unknown rather than claiming every model is new.
    """
    ignored = ignored or {}
    baseline_names = None if baseline is None else _model_names(baseline)
    unregistered = [
        {**record, "recently_published": None if baseline_names is None else record["id"] not in baseline_names}
        for record in eligible_models(litellm_data, today).values()
        if any(_is_open(record["id"], p, registered, ignored) for p in record["providers"])
    ]
    unregistered.sort(key=lambda r: (not r["recently_published"], r["id"]))
    return unregistered[:MAX_NEW_MODELS_PER_RUN], unregistered[MAX_NEW_MODELS_PER_RUN:]


def _is_open(model: str, provider: str, registered: dict[str, set[str]], ignored: dict[str, set[str]]) -> bool:
    return model not in registered.get(provider, set()) and model not in ignored.get(provider, set())


def audit_deprecated_upstream(
    active: set[tuple[str, str]],
    litellm_data: dict[str, Any],
    today: datetime.date | None = None,
) -> list[dict]:
    """Active OCS models whose upstream deprecation date has passed."""
    today = today or datetime.datetime.now(datetime.UTC).date()
    out = []
    for provider, model in sorted(active):
        entry = _litellm_entry(model, litellm_data, provider)
        if entry and _is_deprecated(entry, today):
            out.append({"provider_type": provider, "model_name": model, "deprecation_date": entry["deprecation_date"]})
    return out


# Candidate classification (new-models path)


@dataclass
class PricingResult:
    """Per-1K rates for each provider that has them, keyed by OCS provider."""

    rates_by_provider: dict[str, dict[str, str]]
    source: str | None

    @property
    def has_pricing(self) -> bool:
        return bool(self.rates_by_provider)


@dataclass
class Candidate:
    raw: dict

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def ocs_providers(self) -> list[str]:
        return self.raw.get("providers", [])

    @property
    def keys(self) -> dict[str, str]:
        """The price-table key discovery matched, per provider."""
        return self.raw.get("keys") or {}

    def litellm_key(self, provider: str) -> str:
        return self.keys.get(provider) or self.id

    @property
    def recently_published(self) -> bool | None:
        """None when the baseline was unreachable - unknown, not old."""
        return self.raw.get("recently_published")

    @property
    def source_url(self) -> str:
        return LITELLM_SOURCE_URL

    def registered_providers(self, registered: dict[str, set[str]]) -> list[str]:
        return [p for p in self.ocs_providers if self.id in registered.get(p, set())]

    def is_fully_registered(self, registered: dict[str, set[str]]) -> bool:
        regd = self.registered_providers(registered)
        return bool(regd) and len(regd) == len(self.ocs_providers)


def resolve_pricing(candidate: Candidate, litellm_data: dict[str, Any]) -> PricingResult:
    """Price each provider from its own price-table key.

    A reseller charges its own rate for a model it did not originate.
    """
    rates_by_provider = {}
    for provider in candidate.ocs_providers:
        rates = resolve_pricing_from_litellm(candidate.litellm_key(provider), litellm_data, provider=provider)
        if rates:
            rates_by_provider[provider] = rates
    return PricingResult(rates_by_provider, "litellm" if rates_by_provider else None)


def token_limit_from_litellm(model_id: str, litellm_data: dict[str, Any], provider: str | None = None) -> int | None:
    """Context window for a model, used as ``token_limit`` when registering."""
    entry = _litellm_entry(model_id, litellm_data, provider)
    if entry is None:
        return None
    for field in ("max_input_tokens", "max_tokens"):
        value = entry.get(field)
        if isinstance(value, int) and value > 0:
            return value
    return None


def resolve_token_limits(candidate: Candidate, litellm_data: dict[str, Any]) -> dict[str, int | None]:
    """Context window per provider.

    ``default_models.py`` stores the limit on each provider's own ``Model``
    entry, and resellers differ: Azure serves gpt-5-pro at 272k where OpenAI
    serves 400k.
    """
    return {
        provider: token_limit_from_litellm(candidate.litellm_key(provider), litellm_data, provider=provider)
        for provider in candidate.ocs_providers
    }


def build_model_entry(
    candidate: Candidate,
    registered: dict[str, set[str]],
    priced: set[tuple[str, str]],
    pricing: PricingResult,
    token_limits: dict[str, int | None] | None = None,
) -> tuple[dict, list[dict]]:
    model_id = candidate.id
    providers = candidate.ocs_providers
    entry: dict = {
        "id": model_id,
        "token_limit_by_provider": token_limits or {},
        "recently_published": candidate.recently_published,
        "deprecation_date": candidate.raw.get("deprecation_date"),
        "ocs_providers": providers,
        "already_registered_providers": candidate.registered_providers(registered),
        "already_priced_providers": [p for p in providers if (p, model_id) in priced],
        "source_url": candidate.source_url,
    }
    if not pricing.has_pricing:
        entry["pricing"] = {"has_pricing": False, "source": None, "reason": NO_PRICING_REASON}
        return entry, []
    needs_pricing = {
        provider: rates for provider, rates in pricing.rates_by_provider.items() if (provider, model_id) not in priced
    }
    pricing_entries = build_pricing_entries(model_id, needs_pricing)
    entry["pricing"] = {
        "has_pricing": True,
        "source": pricing.source,
        "unit": "per_1k_tokens",
        "rates_by_provider": pricing.rates_by_provider,
        "unpriced_providers": [p for p in providers if p not in pricing.rates_by_provider],
        "llm_pricing_entries": pricing_entries,
    }
    return entry, pricing_entries


def process_candidates(
    candidates: list[dict],
    registered: dict[str, set[str]],
    priced: set[tuple[str, str]],
    litellm_data: dict[str, Any],
) -> dict:
    new_models: list[dict] = []
    already_registered: list[dict] = []
    unpriced: list[dict] = []
    all_pricing_entries: list[dict] = []

    for raw in candidates:
        candidate = Candidate(raw)
        if candidate.is_fully_registered(registered):
            already_registered.append(
                {
                    "id": candidate.id,
                    "providers": candidate.ocs_providers,
                    "registered_providers": candidate.registered_providers(registered),
                }
            )
            continue

        pricing = resolve_pricing(candidate, litellm_data)
        token_limits = resolve_token_limits(candidate, litellm_data)
        entry, pricing_entries = build_model_entry(candidate, registered, priced, pricing, token_limits)
        all_pricing_entries.extend(pricing_entries)
        new_models.append(entry)

        if not pricing.has_pricing:
            unpriced.append({"id": candidate.id, "ocs_providers": candidate.ocs_providers, "reason": NO_PRICING_REASON})

    return {
        "new_models": new_models,
        "already_registered": already_registered,
        "unpriced_models": unpriced,
        "pricing_entries": all_pricing_entries,
    }


# Rate diff (existing-seed path)


@dataclass(frozen=True)
class RateChange:
    provider_type: str
    model_name: str
    service_kind: str
    old_price: str | None
    new_price: str
    source_url: str


@dataclass(frozen=True)
class _UpstreamRates:
    model_name: str
    new_rates: dict[str, str]
    source_url: str


def diffable_models(index: dict[tuple[str, str], dict[str, str]]) -> set[str]:
    return {model for (provider, model), _ in index.items() if provider in DIFFABLE_PROVIDERS}


def compute_changes(
    index: dict[tuple[str, str], dict[str, str]],
    litellm_data: dict[str, Any],
) -> tuple[list[RateChange], set[str]]:
    """Diff the seed against the LiteLLM price table, emitting a RateChange
    per (provider, service_kind) whose price has moved. ``unmatched`` returns
    models LiteLLM had no usable rates for.

    This makes no network calls of its own: the price table is one file,
    already fetched once per run.
    """
    changes: list[RateChange] = []
    unmatched: set[str] = set()
    for model_name in sorted(diffable_models(index)):
        matched = False
        for provider, seed_rates in _diffable_provider_rates(index, model_name):
            new_rates = resolve_pricing_from_litellm(model_name, litellm_data, provider=provider)
            if not new_rates:
                continue
            matched = True
            upstream = _UpstreamRates(model_name=model_name, new_rates=new_rates, source_url=LITELLM_SOURCE_URL)
            changes.extend(_provider_rate_changes(provider, seed_rates, upstream))
        if not matched:
            unmatched.add(model_name)
    return changes, unmatched


def _diffable_provider_rates(
    index: dict[tuple[str, str], dict[str, str]],
    model_name: str,
) -> list[tuple[str, dict[str, str]]]:
    return [
        (provider, seed_rates)
        for (provider, name), seed_rates in index.items()
        if name == model_name and provider in DIFFABLE_PROVIDERS
    ]


def _provider_rate_changes(
    provider: str,
    seed_rates: dict[str, str],
    upstream: _UpstreamRates,
) -> list[RateChange]:
    return [
        RateChange(
            provider_type=provider,
            model_name=upstream.model_name,
            service_kind=service_kind,
            old_price=seed_rates.get(service_kind),
            new_price=new_price,
            source_url=upstream.source_url,
        )
        for service_kind, new_price in upstream.new_rates.items()
        if _price_differs(seed_rates.get(service_kind), new_price)
    ]


def _price_differs(old: str | None, new: str) -> bool:
    """Numeric comparison so ``0.00250`` vs ``0.0025`` doesn't fire a PR."""
    if old is None:
        return True
    return Decimal(old) != Decimal(new)


# Apply rate changes to seed + generate migration


def apply_changes(seed: list[dict], changes: list[RateChange]) -> list[dict]:
    by_key: dict[tuple[str, str], dict[str, str]] = {(c.provider_type, c.model_name): {} for c in changes}
    for c in changes:
        by_key[(c.provider_type, c.model_name)][c.service_kind] = c.new_price
    return [_apply_to_entry(entry, by_key) for entry in seed]


def _apply_to_entry(entry: dict, updates_by_key: dict[tuple[str, str], dict[str, str]]) -> dict:
    key = (entry["provider_type"], entry["model_name"])
    if key not in updates_by_key:
        return entry
    updated_kinds = updates_by_key[key]
    existing_kinds = {rule["service_kind"] for rule in entry["rules"]}
    new_rules = [_apply_to_rule(rule, updated_kinds) for rule in entry["rules"]]
    for kind, price in updated_kinds.items():
        if kind not in existing_kinds:
            new_rules.append({"service_kind": kind, "unit_price": price})
    return {"provider_type": entry["provider_type"], "model_name": entry["model_name"], "rules": new_rules}


def _apply_to_rule(rule: dict, updated_kinds: dict[str, str]) -> dict:
    return {
        "service_kind": rule["service_kind"],
        "unit_price": updated_kinds.get(rule["service_kind"], rule["unit_price"]),
    }


def generate_migration(migrations_dir: Path, today: datetime.date) -> Path:
    next_num = _next_migration_number(migrations_dir)
    prev_name = _latest_migration_name(migrations_dir)
    filename = f"{next_num:04d}_rate_update_{today.strftime('%Y%m%d')}.py"
    target = migrations_dir / filename
    target.write_text(_migration_template(prev_name))
    return target


def _next_migration_number(migrations_dir: Path) -> int:
    existing = sorted(p.stem for p in migrations_dir.glob("[0-9]*.py"))
    last = existing[-1] if existing else "0000_initial"
    return int(last.split("_", 1)[0]) + 1


def _latest_migration_name(migrations_dir: Path) -> str:
    existing = sorted(p.stem for p in migrations_dir.glob("[0-9]*.py"))
    if not existing:
        raise RuntimeError(f"No existing migrations in {migrations_dir}")
    return existing[-1]


def _migration_template(prev_name: str) -> str:
    return (
        "from django.db import migrations\n\n"
        "from apps.cost_tracking.migration_utils import load_pricing_data\n\n\n"
        "class Migration(migrations.Migration):\n"
        f'    dependencies = [("cost_tracking", "{prev_name}")]\n'
        "    operations = [load_pricing_data()]\n"
    )


# Missing-pricing audit + LiteLLM backfill


@dataclass(frozen=True)
class MissingPricingEntry:
    """One OCS-managed (provider, model) lacking adequate seed pricing."""

    provider_type: str
    model_name: str
    kinds_missing: tuple[str, ...]


def backfill_missing_from_litellm(
    missing: list[MissingPricingEntry],
    litellm_data: dict[str, Any],
) -> tuple[list[dict], list[MissingPricingEntry]]:
    """Try to resolve missing-pricing entries from the LiteLLM price table.

    Each entry is looked up under its own provider, so a reseller gets its own
    rate. Entries whose required kinds all resolve become seed-JSON dicts in
    *backfilled*; the rest stay in *still_missing*.
    """
    backfilled: list[dict] = []
    still_missing: list[MissingPricingEntry] = []

    for entry in missing:
        rates = resolve_pricing_from_litellm(entry.model_name, litellm_data, provider=entry.provider_type)
        if rates and not (REQUIRED_SERVICE_KINDS - rates.keys()):
            rules = [{"service_kind": k, "unit_price": v} for k, v in rates.items()]
            backfilled.append(
                {
                    "provider_type": entry.provider_type,
                    "model_name": entry.model_name,
                    "rules": rules,
                }
            )
        else:
            still_missing.append(entry)

    return backfilled, still_missing


def audit_missing_pricing(
    active: set[tuple[str, str]],
    index: dict[tuple[str, str], dict[str, str]],
) -> list[MissingPricingEntry]:
    """For each (provider, model) in DEFAULT_LLM_PROVIDER_MODELS, report
    which of ``REQUIRED_SERVICE_KINDS`` the seed doesn't cover. A model with
    no seed entry at all counts every required kind as missing.
    """
    out: list[MissingPricingEntry] = []
    for provider, model in sorted(active):
        seed_rates = index.get((provider, model), {})
        missing = tuple(sorted(REQUIRED_SERVICE_KINDS - seed_rates.keys()))
        if missing:
            out.append(MissingPricingEntry(provider, model, missing))
    return out


# PR / issue body rendering


def render_pr_body(changes: list[RateChange], unmatched: set[str], backfilled: list[dict] | None = None) -> str:
    lines = []
    if changes:
        lines += [
            "Detected rate changes in the LiteLLM price table against the in-repo seed.",
            "The data migration loads them on deploy; the seed loader supersedes",
            "each affected `PricingRule` (closes the old row, inserts a fresh one).",
            "",
            "| Provider | Model | Service | Old (per 1K) | New (per 1K) | Source |",
            "| --- | --- | --- | --- | --- | --- |",
            *(_change_row(c) for c in changes),
        ]
    if backfilled:
        if lines:
            lines.append("")
        lines += [
            "## Backfilled from LiteLLM",
            "",
            "The following models had missing or partial seed pricing and were auto-priced using the",
            "[LiteLLM model price table](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json).",
            "Verify the rates before merging.",
            "",
            "| Provider | Model | Service | Price (per 1K) |",
            "| --- | --- | --- | --- |",
            *(_backfill_rows(e) for e in backfilled),
        ]
    lines += _unmatched_section(unmatched)
    if not lines:
        lines = ["No rate changes or new pricing entries."]
    return "\n".join(lines) + "\n"


def _change_row(c: RateChange) -> str:
    old = c.old_price if c.old_price is not None else "-"
    return (
        f"| {c.provider_type} | {c.model_name} | {c.service_kind} | {old} | {c.new_price} | [LiteLLM]({c.source_url}) |"
    )


def _backfill_rows(entry: dict) -> str:
    """Return one table row per pricing rule, matching the 4-column header."""
    return "\n".join(
        f"| {entry['provider_type']} | {entry['model_name']} | {r['service_kind']} | {r['unit_price']} |"
        for r in entry["rules"]
    )


def _unmatched_section(unmatched: set[str]) -> list[str]:
    if not unmatched:
        return []
    return [
        "",
        "## Unmatched models",
        "",
        "These seed models had no usable rate in the LiteLLM price table:",
        *(f"- `{m}`" for m in sorted(unmatched)),
    ]


def render_missing_pricing_issue_body(entries: list[MissingPricingEntry]) -> str:
    """Markdown body for the GitHub issue listing OCS-managed models with
    no seed pricing for ``llm_input`` and/or ``llm_output``.
    """
    lines = [
        "The following OCS-registered models have no usable pricing in",
        "`apps/cost_tracking/seed_data/llm_pricing.json`. The dashboard",
        "cannot compute exact costs for usage of these models until a",
        "seed entry is added.",
        "",
        "Fix by editing the seed (manually, or by running",
        "`manage.py backfill_pricing_seed` and committing the result), then",
        "letting the next deploy's data migration load the new entries.",
        "",
        "| Provider | Model | Missing |",
        "| --- | --- | --- |",
        *(f"| {e.provider_type} | {e.model_name} | {', '.join(e.kinds_missing)} |" for e in entries),
    ]
    return "\n".join(lines) + "\n"


# Reconciliation run bundle


@dataclass(frozen=True)
class _ReconcileResults:
    """All section data produced by one reconciliation pass."""

    candidates: list[dict]
    backlog: list[dict]
    deprecated_upstream: list[dict]
    classification: dict
    changes: list[RateChange]
    unmatched_diff: set[str]
    missing: list[MissingPricingEntry]  # truly unresolvable after litellm backfill
    backfilled: list[dict]  # new seed entries resolved from litellm


# Output assembly + GitHub Actions integration


def _assemble_payload(results: _ReconcileResults, *, run_date: str) -> dict:
    return {
        "run_date": run_date,
        "summary": {
            "candidates": len(results.candidates),
            "backlog": len(results.backlog),
            "deprecated_upstream": len(results.deprecated_upstream),
            "new_models": len(results.classification["new_models"]),
            "already_registered": len(results.classification["already_registered"]),
            "unpriced_candidates": len(results.classification["unpriced_models"]),
            "pricing_entries_generated": len(results.classification["pricing_entries"]),
            "price_changes": len(results.changes),
            "backfilled_from_litellm": len(results.backfilled),
            "missing_pricing": len(results.missing),
        },
        "new_models": results.classification["new_models"],
        "backlog": [{"id": m["id"], "ocs_providers": m["providers"]} for m in results.backlog],
        "deprecated_upstream": results.deprecated_upstream,
        "already_registered": results.classification["already_registered"],
        "unpriced_models": results.classification["unpriced_models"],
        "pricing_entries": results.classification["pricing_entries"],
        "price_changes": [c.__dict__ for c in results.changes],
        "unmatched_diff_models": sorted(results.unmatched_diff),
        "backfilled_pricing": results.backfilled,
        "missing_pricing": [
            {"provider_type": e.provider_type, "model_name": e.model_name, "kinds_missing": list(e.kinds_missing)}
            for e in results.missing
        ],
    }


def _write_github_output(lines: list[str]) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if not gh_out:
        return
    with open(gh_out, "a") as f:
        for line in lines:
            f.write(f"{line}\n")


def _new_models_outputs(new_models: list[dict]) -> list[str]:
    count = len(new_models)
    return [
        f"has_new_models={'true' if count else 'false'}",
        f"new_model_count={count}",
        f"new_model_ids={','.join(m['id'] for m in new_models)}",
    ]


def _price_change_outputs(
    results: _ReconcileResults,
    today: datetime.date,
    body_path: Path | None,
) -> list[str]:
    change_count = len(results.changes)
    backfill_count = len(results.backfilled)
    # The gate opens a PR, so it follows the body being written rather than
    # the changes being found: --dry-run finds them and writes nothing.
    has_any = body_path is not None
    parts = []
    if change_count:
        parts.append(f"{change_count} rate change(s)")
    if backfill_count:
        parts.append(f"{backfill_count} backfilled from LiteLLM")
    title = f"Pricing update: {', '.join(parts)} ({today.isoformat()})" if has_any else ""
    return [
        f"has_price_changes={'true' if has_any else 'false'}",
        f"price_change_count={change_count}",
        f"backfilled_count={backfill_count}",
        f"pricing_pr_title={title}",
        f"pricing_pr_body_path={body_path or ''}",
    ]


def _deprecated_upstream_outputs(deprecated: list[dict]) -> list[str]:
    count = len(deprecated)
    return [
        f"has_deprecated_upstream={'true' if count else 'false'}",
        f"deprecated_upstream_count={count}",
    ]


def _missing_pricing_outputs(missing: list[MissingPricingEntry], body_path: Path | None) -> list[str]:
    count = len(missing)
    return [
        f"has_missing_pricing={'true' if count else 'false'}",
        f"missing_pricing_count={count}",
        f"missing_pricing_issue_body_path={body_path or ''}",
    ]


# CLI


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile OCS model catalogue + pricing seed against upstream sources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--baseline-days",
        type=int,
        default=DEFAULT_BASELINE_DAYS,
        help=f"How far back 'newly published' looks (default: {DEFAULT_BASELINE_DAYS}).",
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."), metavar="PATH")
    parser.add_argument("--output", type=Path, default=Path("reconciliation.json"), metavar="FILE")
    parser.add_argument("--today", help="YYYY-MM-DD override (for tests).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only: leave the pricing seed and migrations untouched.",
    )
    return parser


class UpstreamUnavailable(RuntimeError):
    """The price table could not be read, so the run has nothing to work from."""


def _load_litellm() -> dict[str, Any]:
    try:
        data = _get_json(LITELLM_PRICING_URL)
    except Exception as exc:
        raise UpstreamUnavailable(f"could not fetch the LiteLLM price table: {exc}") from exc
    if not data:
        raise UpstreamUnavailable("the LiteLLM price table came back empty")
    print(f"  -> {len(data)} LiteLLM entries")
    return data


def _commit_price_changes(
    results: _ReconcileResults,
    repo_root: Path,
    output_path: Path,
    today: datetime.date,
    dry_run: bool = False,
) -> Path | None:
    """Rewrite seed JSON + emit rate-update migration + write PR body.

    Handles both upstream rate changes and LiteLLM-backfilled entries.  A
    migration is generated whenever *either* list is non-empty so both kinds
    of update are applied in the same deploy.
    No-op (returns None) when both lists are empty.
    """
    if not results.changes and not results.backfilled:
        return None
    if dry_run:
        print(
            f"  -> dry run: would rewrite the seed and emit a migration "
            f"({len(results.changes)} rate change(s), {len(results.backfilled)} backfilled)"
        )
        return None
    seed_path = repo_root / LLM_PRICING_REL_PATH
    updated = apply_changes(load_seed(seed_path), results.changes)
    updated = _merge_backfilled(updated, results.backfilled)
    seed_path.write_text(json.dumps(updated, indent=2) + "\n")
    migration_path = generate_migration(repo_root / MIGRATIONS_DIR_REL_PATH, today)
    print(f"  -> wrote {migration_path.name} + updated seed")
    body_path = output_path.with_name(output_path.stem + ".pricing-body.md")
    body_path.write_text(render_pr_body(results.changes, results.unmatched_diff, results.backfilled))
    return body_path


def _merge_backfilled(seed: list[dict], backfilled: list[dict]) -> list[dict]:
    """Add LiteLLM-backfilled entries to the seed, filling gaps only.

    A model may already have a partial entry (e.g. only llm_cached_input); its
    rules are updated in place rather than the backfilled data being dropped.
    Prices already curated in the seed are never overwritten.
    """
    entry_index = {(e["provider_type"], e["model_name"]): i for i, e in enumerate(seed)}
    for bf_entry in backfilled:
        key = (bf_entry["provider_type"], bf_entry["model_name"])
        if key not in entry_index:
            seed.append(bf_entry)
            continue
        existing = seed[entry_index[key]]
        rules_by_kind = {r["service_kind"]: r for r in existing["rules"]}
        for rule in bf_entry["rules"]:
            rules_by_kind.setdefault(rule["service_kind"], rule)
        existing["rules"] = list(rules_by_kind.values())
    return seed


def _write_missing_body(results: _ReconcileResults, output_path: Path) -> Path | None:
    if not results.missing:
        return None
    body_path = output_path.with_name(output_path.stem + ".missing-pricing-body.md")
    body_path.write_text(render_missing_pricing_issue_body(results.missing))
    return body_path


def _run_reconciliation(
    repo_root: Path,
    baseline_days: int = DEFAULT_BASELINE_DAYS,
    today: datetime.date | None = None,
) -> _ReconcileResults:
    """Fetch + classify + diff + audit. The print()s narrate progress for CI logs."""
    today = today or datetime.datetime.now(datetime.UTC).date()
    print("  Loading registered models ...")
    registered = load_registered_models(repo_root)
    active = load_active_default_models(repo_root)
    ignored = load_ignored_models(repo_root)
    print(f"  -> {sum(len(v) for v in registered.values())} registered entries; {len(active)} active OCS models")
    print(f"  -> {sum(len(v) for v in ignored.values())} model(s) previously considered and skipped")

    print("  Loading existing pricing seed ...")
    index = seed_index(load_seed(repo_root / LLM_PRICING_REL_PATH))
    priced = set(index)
    print(f"  -> {len(priced)} priced (provider, model) pairs")

    print("  Fetching the LiteLLM price table ...")
    litellm_data = _load_litellm()

    print(f"  Reading the price table as it stood {baseline_days} day(s) ago ...")
    baseline = fetch_baseline(baseline_days, today)
    candidates, backlog = select_candidates(litellm_data, baseline, registered, ignored, today)
    recent = sum(1 for c in candidates if c["recently_published"])
    print(f"  -> {len(candidates)} candidate(s) ({recent} newly published), {len(backlog)} in backlog")
    classification = process_candidates(candidates, registered, priced, litellm_data)

    print("  Diffing seed against the LiteLLM price table ...")
    changes, unmatched_diff = compute_changes(index, litellm_data)
    print(f"  -> {len(changes)} rate change(s); {len(unmatched_diff)} unmatched")

    print("  Auditing missing pricing for OCS-managed models ...")
    all_missing = audit_missing_pricing(active, index)
    print(f"  -> {len(all_missing)} model(s) missing seed pricing; attempting LiteLLM backfill ...")
    backfilled, still_missing = backfill_missing_from_litellm(all_missing, litellm_data)
    print(f"  -> backfilled {len(backfilled)}, still missing {len(still_missing)}")

    print("  Checking active models against upstream deprecation dates ...")
    deprecated = audit_deprecated_upstream(active, litellm_data, today)
    print(f"  -> {len(deprecated)} active model(s) past their upstream deprecation date")

    return _ReconcileResults(
        candidates=candidates,
        backlog=backlog,
        deprecated_upstream=deprecated,
        classification=classification,
        changes=changes,
        unmatched_diff=unmatched_diff,
        missing=still_missing,
        backfilled=backfilled,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    repo_root: Path = args.repo_root.resolve()
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.datetime.now(datetime.UTC).date()
    # Unbuffered, so the CI log interleaves progress with rate-limit sleeps in
    # real time. Block-buffered output flushes at exit and reads as if every
    # step ran in the same millisecond.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    print(f"[reconcile-models] repo_root={repo_root}")

    try:
        results = _run_reconciliation(repo_root, args.baseline_days, today)
    except UpstreamUnavailable as exc:
        print(f"  (!) {exc}")
        return 1
    pricing_body_path = _commit_price_changes(results, repo_root, args.output, today, args.dry_run)
    missing_body_path = _write_missing_body(results, args.output)

    payload = _assemble_payload(results, run_date=datetime.datetime.now(datetime.UTC).isoformat())

    args.output.write_text(json.dumps(payload, indent=2))

    print()
    print(f"  Output -> {args.output}")
    for key, value in payload["summary"].items():
        print(f"  {key}: {value}")

    _write_github_output(
        _new_models_outputs(results.classification["new_models"])
        + _price_change_outputs(results, today, pricing_body_path)
        + _missing_pricing_outputs(results.missing, missing_body_path)
        + _deprecated_upstream_outputs(results.deprecated_upstream)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
