#!/usr/bin/env python3
"""
Reconcile OCS's in-repo model catalogue and pricing seed against upstream
sources (llm-stats.com via the zeroeval Stats API, with LiteLLM as a fallback
for pricing). One daily run produces three signals consumed by the
``auto-update-models`` workflow:

* **new_models** - candidates newly published upstream that OCS hasn't
  registered yet. Feeds the Claude Code job that opens a "Register new models"
  PR.
* **price_changes** - existing seed entries whose upstream rate has moved.
  Rewrites ``llm_pricing.json`` in place and emits a
  ``NNNN_rate_update_YYYYMMDD.py`` data migration, so the workflow can open a
  mechanical "Pricing update" PR.
* **missing_pricing** - models in ``default_models.py`` with no usable seed
  entry (no ``llm_input``/``llm_output`` rule). Feeds a "missing pricing"
  GitHub issue so OCS-managed coverage gaps surface as a tracked task.

Usage (from the repo root)::

    python scripts/reconcile_models.py \\
        --bearer-token "$LLM_STATS_BEARER_TOKEN" \\
        [--days 1] \\
        [--repo-root .] \\
        [--output reconciliation.json] \\
        [--today YYYY-MM-DD]    # deterministic-tests override

Side effects (only when ``price_changes`` is non-empty):

* Overwrites ``apps/cost_tracking/seed_data/llm_pricing.json`` with the new
  rates.
* Writes a new migration at
  ``apps/cost_tracking/migrations/NNNN_rate_update_YYYYMMDD.py``.

Side effects (always, when running under GitHub Actions):

* Appends gate variables to ``$GITHUB_OUTPUT``::

    has_new_models, new_model_count, new_model_ids
    has_price_changes, price_change_count, pricing_pr_title, pricing_pr_body_path
    has_missing_pricing, missing_pricing_count, missing_pricing_issue_body_path

Pricing unit conventions
------------------------
* OCS ``llm_pricing.json`` stores ``unit_price`` per **1 000** tokens.
* llm-stats.com returns ``input_price``/``output_price`` per **1 000 000**
  tokens (divide by 1 000).
* LiteLLM's ``model_prices_and_context_window.json`` is per **1** token
  (multiply by 1 000).
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

# Constants

ORG_TO_OCS_PROVIDERS: dict[str, list[str]] = {
    "openai": ["openai", "azure"],
    "anthropic": ["anthropic"],
    "google": ["google", "google_vertex_ai"],
    "deepseek": ["deepseek"],
    "perplexity": ["perplexity"],
    "minimax": ["minimax"],
}

# Providers we can diff against llm-stats. llm-stats prices an upstream
# model once (e.g. "gpt-4o"); the same rate applies to OCS providers that
# wrap that upstream. Others (groq, deepseek, ...) have no llm-stats source.
DIFFABLE_PROVIDERS = frozenset({"openai", "azure", "anthropic", "google", "google_vertex_ai"})

# A model with neither llm_input nor llm_output seed pricing is treated as
# missing, regardless of whether a cached-input rate exists.
REQUIRED_SERVICE_KINDS = frozenset({"llm_input", "llm_output"})

LITELLM_PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
LLM_STATS_DETAIL_URL = "https://api.zeroeval.com/stats/v1/models/{model_id}"
LLM_STATS_UPDATES_URL = "https://api.zeroeval.com/stats/v1/updates?days={days}&limit=30"
LLM_STATS_PUBLIC_URL = "https://llm-stats.com/models/{model_id}"

DEFAULT_MODELS_REL_PATH = "apps/service_providers/llm_service/default_models.py"
LLM_PRICING_REL_PATH = "apps/cost_tracking/seed_data/llm_pricing.json"
MIGRATIONS_DIR_REL_PATH = "apps/cost_tracking/migrations"

_NOISY_DETAIL_FIELDS = {"scores", "top_scores", "providers"}

_DETAIL_PRICE_FIELDS = {
    "input_price": "llm_input",
    "output_price": "llm_output",
    "cached_input_price": "llm_cached_input",
    "cache_write_price": "llm_cache_write",
}

NO_PRICING_REASON = "No pricing data found in llm_stats or LiteLLM"

# HTTP helpers


def _get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _api_get(url: str, bearer: str) -> Any:
    return _get_json(
        url,
        headers={
            "Authorization": f"Bearer {bearer}",
            "User-Agent": "ocs-reconcile-models-script/1.0",
            "Accept": "application/json",
        },
    )


# default_models.py parsing - pure AST, no Django import


def _model_call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call) and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _const_str(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _default_model_pairs(dict_node: ast.Dict) -> Iterator[tuple[str, str]]:
    for key_node, value_node in zip(dict_node.keys, dict_node.values, strict=True):
        provider = _const_str(key_node) if key_node is not None else None
        if provider is None or not isinstance(value_node, ast.List):
            continue
        for name in filter(None, map(_model_call_name, value_node.elts)):
            yield provider, name


def _deleted_model_pairs(list_node: ast.List) -> Iterator[tuple[str, str]]:
    for elt in list_node.elts:
        if not (isinstance(elt, ast.Tuple) and len(elt.elts) >= 2):
            continue
        provider, model = _const_str(elt.elts[0]), _const_str(elt.elts[1])
        if provider is not None and model is not None:
            yield provider, model


def _assignment_pairs(node: ast.stmt) -> Iterator[tuple[str, str]]:
    if not isinstance(node, ast.Assign):
        return
    targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
    if "DEFAULT_LLM_PROVIDER_MODELS" in targets and isinstance(node.value, ast.Dict):
        yield from _default_model_pairs(node.value)
    elif "DELETED_MODELS" in targets and isinstance(node.value, ast.List):
        yield from _deleted_model_pairs(node.value)


def load_registered_models(repo_root: Path) -> dict[str, set[str]]:
    """Parse ``default_models.py`` and return ``{provider: {model_name, ...}}``.

    DELETED_MODELS entries are folded in so re-adding a deleted model is
    flagged for review rather than silently re-registered.
    """
    tree = ast.parse((repo_root / DEFAULT_MODELS_REL_PATH).read_text())
    registered: dict[str, set[str]] = {}
    for node in tree.body:
        for provider, model in _assignment_pairs(node):
            registered.setdefault(provider, set()).add(model)
    return registered


def load_active_default_models(repo_root: Path) -> set[tuple[str, str]]:
    """``(provider, model)`` pairs in ``DEFAULT_LLM_PROVIDER_MODELS`` only.

    The missing-pricing audit consumes this. DELETED_MODELS are deliberately
    excluded - the audit only flags coverage gaps for *active* OCS models.
    """
    tree = ast.parse((repo_root / DEFAULT_MODELS_REL_PATH).read_text())
    active: set[tuple[str, str]] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if "DEFAULT_LLM_PROVIDER_MODELS" in targets and isinstance(node.value, ast.Dict):
            active.update(_default_model_pairs(node.value))
    return active


# Seed I/O


def load_seed(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def load_priced_models(repo_root: Path) -> set[tuple[str, str]]:
    return {(entry["provider_type"], entry["model_name"]) for entry in load_seed(repo_root / LLM_PRICING_REL_PATH)}


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


def _per_million_to_per_1k(v: float | None) -> float | None:
    return v / 1_000.0 if v is not None else None


def _per_token_to_per_1k(v: float | None) -> float | None:
    return v * 1_000.0 if v is not None else None


def _format_per_1k(per_million: float) -> str:
    return f"{per_million / 1000:.8f}".rstrip("0").rstrip(".") or "0"


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


def resolve_pricing_from_llm_stats(details: dict[str, Any]) -> dict[str, str] | None:
    return _rates_from_raw(
        {
            "llm_input": details.get("input_price"),
            "llm_output": details.get("output_price"),
            "llm_cached_input": details.get("cached_input_price"),
            "llm_cache_write": details.get("cache_write_price"),
        },
        _per_million_to_per_1k,
    )


def resolve_pricing_from_litellm(
    model_id: str,
    litellm_data: dict[str, Any],
    provider: str | None = None,
) -> dict[str, str] | None:
    """Look up ``model_id`` in the LiteLLM pricing table.

    Tries the bare model ID first, then ``{provider}/{model_id}`` when a
    provider is supplied.  This catches provider-namespaced entries such as
    ``groq/gemma-7b-it`` that would be missed by a bare lookup.
    """
    candidates = [model_id]
    if provider:
        candidates.append(f"{provider}/{model_id}")
    for key in candidates:
        entry = litellm_data.get(key)
        if entry is not None:
            return _rates_from_raw(
                {
                    "llm_input": entry.get("input_cost_per_token"),
                    "llm_output": entry.get("output_cost_per_token"),
                    "llm_cached_input": entry.get("cache_read_input_token_cost"),
                    "llm_cache_write": entry.get("cache_creation_input_token_cost"),
                },
                _per_token_to_per_1k,
            )
    return None


def rates_from_detail(detail: dict) -> dict[str, str]:
    """Per-1K-token rates from an llm-stats detail payload."""
    rates: dict[str, str] = {}
    for detail_key, service_kind in _DETAIL_PRICE_FIELDS.items():
        raw = detail.get(detail_key)
        if raw is None:
            continue
        rates[service_kind] = _format_per_1k(raw)
    return rates


def build_pricing_entries(model_id: str, providers: list[str], pricing: dict[str, str]) -> list[dict]:
    rules = [{"service_kind": kind, "unit_price": price} for kind, price in pricing.items()]
    return [{"provider_type": provider, "model_name": model_id, "rules": rules} for provider in providers if rules]


# llm-stats fetch helpers


def _filter_matched_models(updates: dict) -> list[dict]:
    return [
        m
        for m in updates.get("models", [])
        if m.get("organization", {}).get("id") in ORG_TO_OCS_PROVIDERS and m.get("model_type") == "llm"
    ]


def _enrich_model(model: dict, bearer: str) -> dict:
    """Hydrate ``model`` in-place with its detail payload (or ``details_error``)."""
    model_id = model["id"]
    try:
        details = _api_get(LLM_STATS_DETAIL_URL.format(model_id=model_id), bearer)
        for field in _NOISY_DETAIL_FIELDS:
            details.pop(field, None)
        model["details"] = details
        if details.get("context_window") and not model.get("context_window"):
            model["context_window"] = details["context_window"]
    except urllib.error.HTTPError as e:
        model["details_error"] = f"HTTP {e.code}: {e.reason}"
    except Exception as e:  # noqa: BLE001
        model["details_error"] = str(e)
    return model


def fetch_candidates(bearer: str, days: int) -> list[dict]:
    updates = _api_get(LLM_STATS_UPDATES_URL.format(days=days), bearer)
    return [_enrich_model(m, bearer) for m in _filter_matched_models(updates)]


def fetch_detail(model_id: str, bearer: str) -> dict | None:
    try:
        return _api_get(LLM_STATS_DETAIL_URL.format(model_id=model_id), bearer)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


# Candidate classification (new-models path)


@dataclass
class PricingResult:
    rates: dict[str, str] | None
    source: str | None

    @property
    def has_pricing(self) -> bool:
        return bool(self.rates)


@dataclass
class Candidate:
    raw: dict

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def org(self) -> str:
        return self.raw.get("organization", {}).get("id", "")

    @property
    def details(self) -> dict:
        return self.raw.get("details", {})

    @property
    def details_error(self) -> str | None:
        return self.raw.get("details_error")

    @property
    def context_window(self) -> int | None:
        return self.raw.get("context_window")

    @property
    def ocs_providers(self) -> list[str]:
        return ORG_TO_OCS_PROVIDERS.get(self.org, [])

    @property
    def source_url(self) -> str:
        return self.details.get("url") or LLM_STATS_PUBLIC_URL.format(model_id=self.id)

    def registered_providers(self, registered: dict[str, set[str]]) -> list[str]:
        return [p for p in self.ocs_providers if self.id in registered.get(p, set())]

    def is_fully_registered(self, registered: dict[str, set[str]]) -> bool:
        regd = self.registered_providers(registered)
        return bool(regd) and len(regd) == len(self.ocs_providers)


def resolve_pricing(candidate: Candidate, litellm_data: dict[str, Any]) -> PricingResult:
    if not candidate.details_error:
        rates = resolve_pricing_from_llm_stats(candidate.details)
        if rates:
            return PricingResult(rates, "llm_stats")
    # Pass the upstream org (e.g. "groq") so provider-namespaced keys like
    # "groq/gemma-7b-it" are tried as a fallback.
    rates = resolve_pricing_from_litellm(candidate.id, litellm_data, provider=candidate.org)
    return PricingResult(rates, "litellm" if rates else None)


def build_model_entry(
    candidate: Candidate,
    registered: dict[str, set[str]],
    priced: set[tuple[str, str]],
    pricing: PricingResult,
) -> tuple[dict, list[dict]]:
    model_id = candidate.id
    providers = candidate.ocs_providers
    entry: dict = {
        "id": model_id,
        "org": candidate.org,
        "context_window": candidate.context_window,
        "ocs_providers": providers,
        "already_registered_providers": candidate.registered_providers(registered),
        "already_priced_providers": [p for p in providers if (p, model_id) in priced],
        "source_url": candidate.source_url,
        "sources": candidate.details.get("sources", {}),
        "details_error": candidate.details_error,
    }
    if not pricing.has_pricing:
        entry["pricing"] = {"has_pricing": False, "source": None, "reason": NO_PRICING_REASON}
        return entry, []
    needs_pricing = [p for p in providers if (p, model_id) not in priced]
    pricing_entries = build_pricing_entries(model_id, needs_pricing, pricing.rates)
    entry["pricing"] = {
        "has_pricing": True,
        "source": pricing.source,
        "unit": "per_1k_tokens",
        "rates": pricing.rates,
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
                    "org": candidate.org,
                    "registered_providers": candidate.registered_providers(registered),
                }
            )
            continue

        pricing = resolve_pricing(candidate, litellm_data)
        entry, pricing_entries = build_model_entry(candidate, registered, priced, pricing)
        all_pricing_entries.extend(pricing_entries)
        new_models.append(entry)

        if not pricing.has_pricing:
            unpriced.append(
                {
                    "id": candidate.id,
                    "org": candidate.org,
                    "ocs_providers": candidate.ocs_providers,
                    "reason": NO_PRICING_REASON,
                }
            )

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
    fetcher: Callable[[str], dict | None],
) -> tuple[list[RateChange], set[str]]:
    """For each diffable model, fetch upstream rates and emit a RateChange
    per (provider, service_kind) whose price has moved. ``unmatched`` returns
    models the fetcher had no usable data for.
    """
    changes: list[RateChange] = []
    unmatched: set[str] = set()
    for model_name in sorted(diffable_models(index)):
        detail = fetcher(model_name)
        if detail is None:
            unmatched.add(model_name)
            continue
        new_rates = rates_from_detail(detail)
        if not new_rates:
            unmatched.add(model_name)
            continue
        source_url = detail.get("url") or LLM_STATS_PUBLIC_URL.format(model_id=model_name)
        upstream = _UpstreamRates(model_name=model_name, new_rates=new_rates, source_url=source_url)
        changes.extend(_changes_for_model(index, upstream))
    return changes, unmatched


def _changes_for_model(
    index: dict[tuple[str, str], dict[str, str]],
    upstream: _UpstreamRates,
) -> list[RateChange]:
    out: list[RateChange] = []
    for provider, seed_rates in _diffable_provider_rates(index, upstream.model_name):
        out.extend(_provider_rate_changes(provider, seed_rates, upstream))
    return out


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

    For each entry in *missing*, attempts a litellm lookup (bare model ID first,
    then ``provider/model_id`` as a fallback).  Entries whose required kinds are
    all resolved are converted into seed-JSON dicts and returned in
    *backfilled*; anything that can't be fully resolved stays in *still_missing*.

    Returns:
        (backfilled, still_missing)
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
            "Detected rate changes on llm-stats.com against the in-repo seed.",
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
        f"| {c.provider_type} | {c.model_name} | {c.service_kind} | "
        f"{old} | {c.new_price} | [llm-stats]({c.source_url}) |"
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
        "These seed models had no usable rate on llm-stats.com:",
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
    classification: dict
    changes: list[RateChange]
    unmatched_diff: set[str]
    missing: list[MissingPricingEntry]  # truly unresolvable after litellm backfill
    backfilled: list[dict]  # new seed entries resolved from litellm


# Output assembly + GitHub Actions integration


def _assemble_payload(results: _ReconcileResults, *, run_date: str, days_lookback: int) -> dict:
    return {
        "run_date": run_date,
        "days_lookback": days_lookback,
        "summary": {
            "candidates_from_api": len(results.candidates),
            "new_models": len(results.classification["new_models"]),
            "already_registered": len(results.classification["already_registered"]),
            "unpriced_candidates": len(results.classification["unpriced_models"]),
            "pricing_entries_generated": len(results.classification["pricing_entries"]),
            "price_changes": len(results.changes),
            "backfilled_from_litellm": len(results.backfilled),
            "missing_pricing": len(results.missing),
        },
        "new_models": results.classification["new_models"],
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
    has_any = bool(change_count or backfill_count)
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
    parser.add_argument("--bearer-token", required=True, metavar="TOKEN")
    parser.add_argument("--days", type=int, default=1, help="Lookback window in days (default: 1, max: 30).")
    parser.add_argument("--repo-root", type=Path, default=Path("."), metavar="PATH")
    parser.add_argument("--output", type=Path, default=Path("reconciliation.json"), metavar="FILE")
    parser.add_argument("--today", help="YYYY-MM-DD override (for tests).")
    return parser


def _load_litellm() -> dict[str, Any]:
    try:
        data = _get_json(LITELLM_PRICING_URL)
        print(f"  -> {len(data)} LiteLLM entries")
        return data
    except Exception as e:  # noqa: BLE001
        print(f"  (!) Could not fetch LiteLLM pricing ({e}); fallback disabled.")
        return {}


def _commit_price_changes(
    results: _ReconcileResults,
    repo_root: Path,
    output_path: Path,
    today: datetime.date,
) -> Path | None:
    """Rewrite seed JSON + emit rate-update migration + write PR body.

    Handles both upstream rate changes and LiteLLM-backfilled entries.  A
    migration is generated whenever *either* list is non-empty so both kinds
    of update are applied in the same deploy.
    No-op (returns None) when both lists are empty.
    """
    if not results.changes and not results.backfilled:
        return None
    seed_path = repo_root / LLM_PRICING_REL_PATH
    seed = load_seed(seed_path)
    updated = apply_changes(seed, results.changes)
    if results.backfilled:
        # Merge backfilled entries into the seed.  A model may already have a
        # partial entry (e.g. only llm_cached_input); in that case we update its
        # rules in place rather than silently skipping the backfilled data.
        entry_index: dict[tuple[str, str], int] = {
            (e["provider_type"], e["model_name"]): i for i, e in enumerate(updated)
        }
        for bf_entry in results.backfilled:
            key = (bf_entry["provider_type"], bf_entry["model_name"])
            if key in entry_index:
                existing = updated[entry_index[key]]
                rules_by_kind = {r["service_kind"]: r for r in existing["rules"]}
                for rule in bf_entry["rules"]:
                    # Only fill gaps; never overwrite prices already curated in the seed.
                    rules_by_kind.setdefault(rule["service_kind"], rule)
                existing["rules"] = list(rules_by_kind.values())
            else:
                updated.append(bf_entry)
    seed_path.write_text(json.dumps(updated, indent=2) + "\n")
    migration_path = generate_migration(repo_root / MIGRATIONS_DIR_REL_PATH, today)
    print(f"  -> wrote {migration_path.name} + updated seed")
    body_path = output_path.with_name(output_path.stem + ".pricing-body.md")
    body_path.write_text(render_pr_body(results.changes, results.unmatched_diff, results.backfilled))
    return body_path


def _write_missing_body(results: _ReconcileResults, output_path: Path) -> Path | None:
    if not results.missing:
        return None
    body_path = output_path.with_name(output_path.stem + ".missing-pricing-body.md")
    body_path.write_text(render_missing_pricing_issue_body(results.missing))
    return body_path


def _run_reconciliation(repo_root: Path, bearer: str, days: int) -> _ReconcileResults:
    """Fetch + classify + diff + audit. The print()s narrate progress for CI logs."""
    print("  Loading registered models ...")
    registered = load_registered_models(repo_root)
    active = load_active_default_models(repo_root)
    print(f"  -> {sum(len(v) for v in registered.values())} registered entries; {len(active)} active OCS models")

    print("  Loading existing pricing seed ...")
    index = seed_index(load_seed(repo_root / LLM_PRICING_REL_PATH))
    priced = set(index)
    print(f"  -> {len(priced)} priced (provider, model) pairs")

    print("  Fetching LiteLLM pricing fallback ...")
    litellm_data = _load_litellm()

    print(f"  Fetching candidates from zeroeval API (days={days}) ...")
    candidates = fetch_candidates(bearer, days)
    print(f"  -> {len(candidates)} candidate(s) matched to OCS orgs")
    classification = process_candidates(candidates, registered, priced, litellm_data)

    print("  Diffing seed against llm-stats current pricing ...")
    changes, unmatched_diff = compute_changes(index, lambda mid: fetch_detail(mid, bearer))
    print(f"  -> {len(changes)} rate change(s); {len(unmatched_diff)} unmatched")

    print("  Auditing missing pricing for OCS-managed models ...")
    all_missing = audit_missing_pricing(active, index)
    print(f"  -> {len(all_missing)} model(s) missing seed pricing; attempting LiteLLM backfill ...")
    backfilled, still_missing = backfill_missing_from_litellm(all_missing, litellm_data)
    print(f"  -> backfilled {len(backfilled)}, still missing {len(still_missing)}")

    return _ReconcileResults(candidates, classification, changes, unmatched_diff, still_missing, backfilled)


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    repo_root: Path = args.repo_root.resolve()
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    print(f"[reconcile-models] repo_root={repo_root}")

    results = _run_reconciliation(repo_root, args.bearer_token, args.days)
    pricing_body_path = _commit_price_changes(results, repo_root, args.output, today)
    missing_body_path = _write_missing_body(results, args.output)

    payload = _assemble_payload(
        results,
        run_date=datetime.datetime.now(datetime.UTC).isoformat(),
        days_lookback=args.days,
    )

    args.output.write_text(json.dumps(payload, indent=2))

    print()
    print(f"  Output -> {args.output}")
    for key, value in payload["summary"].items():
        print(f"  {key}: {value}")

    _write_github_output(
        _new_models_outputs(results.classification["new_models"])
        + _price_change_outputs(results, today, pricing_body_path)
        + _missing_pricing_outputs(results.missing, missing_body_path)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
