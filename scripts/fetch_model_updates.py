#!/usr/bin/env python3
"""
Fetch new LLM model updates from llm-stats.com (via the zeroeval Stats API),
dedup against models already registered in OCS, resolve pricing from llm-stats
and LiteLLM as a fallback, then emit structured JSON for the auto-update-models
workflow to consume.

Usage (inside the cloned repo root)::

    python scripts/fetch_model_updates.py \\
        --bearer-token "$LLM_STATS_BEARER_TOKEN" \\
        [--days 1] \\
        [--repo-root .] \\
        [--output candidate_models.json]

The script is read-only with respect to OCS source files.  It only *reads*
``default_models.py`` and ``llm_pricing.json`` for dedup and writes the
structured output JSON at ``--output``.

Output format (candidate_models.json)
--------------------------------------
::

    {
      "run_date": "2026-06-17T04:00:00Z",
      "days_lookback": 1,
      "summary": {
        "candidates_from_api": 3,
        "new_models": 2,
        "already_registered": 1,
        "unpriced": 0
      },
      "new_models": [
        {
          "id": "gpt-new",
          "org": "openai",
          "context_window": 128000,
          "ocs_providers": ["openai", "azure"],
          "already_registered_providers": [],
          "already_priced_providers": [],
          "source_url": "https://llm-stats.com/models/gpt-new",
          "sources": { ... },
          "details_error": null,
          "pricing": {
            "has_pricing": true,
            "source": "llm_stats",   // or "litellm"
            "unit": "per_1k_tokens",
            "rates": {
              "llm_input": "0.0025",
              "llm_output": "0.01",
              "llm_cached_input": "0.00125"
            },
            "llm_pricing_entries": [
              {
                "provider_type": "openai",
                "model_name": "gpt-new",
                "rules": [
                  {"service_kind": "llm_input", "unit_price": "0.0025"},
                  ...
                ]
              }
            ]
          }
        }
      ],
      "already_registered": [ ... ],
      "unpriced_models": [ ... ],
      "pricing_entries": [ ... ]   // flat list, ready to append to llm_pricing.json
    }

Pricing unit conventions
------------------------
* OCS ``llm_pricing.json`` stores ``unit_price`` as **price per 1 000 tokens**.
* llm-stats.com returns ``input_price`` / ``output_price`` **per 1 000 000 tokens**
  → divide by 1 000.
* LiteLLM's ``model_prices_and_context_window.json`` uses ``input_cost_per_token``
  **per 1 token** → multiply by 1 000.

The conversions happen in ``_per_million_to_per_1k`` and ``_per_token_to_per_1k``.
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# llm-stats.com organisation IDs → OCS provider keys that map to them.
# Keep this in sync with the OCS_ORGS set in the workflow YAML and with
# the keys in DEFAULT_LLM_PROVIDER_MODELS inside default_models.py.
ORG_TO_OCS_PROVIDERS: dict[str, list[str]] = {
    "openai": ["openai", "azure"],
    "anthropic": ["anthropic"],
    "google": ["google", "google_vertex_ai"],
    "deepseek": ["deepseek"],
    "perplexity": ["perplexity"],
}

LITELLM_PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"

DEFAULT_MODELS_REL_PATH = "apps/service_providers/llm_service/default_models.py"
LLM_PRICING_REL_PATH = "apps/cost_tracking/seed_data/llm_pricing.json"

# Detail-response fields that add noise without helping the agent.
_NOISY_DETAIL_FIELDS = {"scores", "top_scores", "providers"}

# Shown for any model where neither source carried pricing.
NO_PRICING_REASON = "No pricing data found in llm_stats or LiteLLM"

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    """Fetch *url* and decode the response body as JSON."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _api_get(url: str, bearer: str) -> Any:
    """GET *url* with a Bearer-token auth header and return decoded JSON."""
    return _get_json(
        url,
        headers={
            "Authorization": f"Bearer {bearer}",
            "User-Agent": "ocs-auto-models-script/1.0",
            "Accept": "application/json",
        },
    )


# ---------------------------------------------------------------------------
# Dedup helpers — parse OCS source files without importing Django
# ---------------------------------------------------------------------------


def _model_call_name(node: ast.expr) -> str | None:
    """Return the first positional string arg of a ``Model(...)`` call, else None."""
    if isinstance(node, ast.Call) and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _const_str(node: ast.expr) -> str | None:
    """Return the value of a string-constant node, else None."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _default_model_pairs(dict_node: ast.Dict) -> Iterator[tuple[str, str]]:
    """Yield ``(provider, model_name)`` for every ``Model(...)`` in the provider dict."""
    for key_node, value_node in zip(dict_node.keys, dict_node.values, strict=True):
        provider = _const_str(key_node) if key_node is not None else None
        if provider is None or not isinstance(value_node, ast.List):
            continue
        for name in filter(None, map(_model_call_name, value_node.elts)):
            yield provider, name


def _deleted_model_pairs(list_node: ast.List) -> Iterator[tuple[str, str]]:
    """Yield ``(provider, model_name)`` from each DELETED_MODELS tuple.

    Entries are 2- or 3-tuples (``(provider, model[, replacement])``); only the
    first two string elements are read, so longer tuples are handled too.
    """
    for elt in list_node.elts:
        if not (isinstance(elt, ast.Tuple) and len(elt.elts) >= 2):
            continue
        provider, model = _const_str(elt.elts[0]), _const_str(elt.elts[1])
        if provider is not None and model is not None:
            yield provider, model


def _assignment_pairs(node: ast.stmt) -> Iterator[tuple[str, str]]:
    """Yield ``(provider, model_name)`` for a DEFAULT_LLM_PROVIDER_MODELS or
    DELETED_MODELS assignment; nothing for anything else.
    """
    if not isinstance(node, ast.Assign):
        return
    targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
    if "DEFAULT_LLM_PROVIDER_MODELS" in targets and isinstance(node.value, ast.Dict):
        yield from _default_model_pairs(node.value)
    elif "DELETED_MODELS" in targets and isinstance(node.value, ast.List):
        yield from _deleted_model_pairs(node.value)


def load_registered_models(repo_root: Path) -> dict[str, set[str]]:
    """Parse ``default_models.py`` and return ``{provider: {model_name, ...}}``.

    Also captures entries from DELETED_MODELS so that re-adding a deleted
    model is treated as *known* and flagged for review rather than silently
    re-added as a brand-new model.
    """
    tree = ast.parse((repo_root / DEFAULT_MODELS_REL_PATH).read_text())
    registered: dict[str, set[str]] = {}
    for node in tree.body:
        for provider, model in _assignment_pairs(node):
            registered.setdefault(provider, set()).add(model)
    return registered


def load_priced_models(repo_root: Path) -> set[tuple[str, str]]:
    """Return ``{(provider_type, model_name)}`` pairs in ``llm_pricing.json``."""
    data = json.loads((repo_root / LLM_PRICING_REL_PATH).read_text())
    return {(entry["provider_type"], entry["model_name"]) for entry in data}


# ---------------------------------------------------------------------------
# Pricing resolution
# ---------------------------------------------------------------------------


def _fmt(value: float | None) -> str | None:
    """Format a per-1K-token price as a compact decimal string.

    Uses up to 8 significant digits and strips trailing zeros so that e.g.
    ``0.00250000`` → ``"0.0025"`` and ``0.000075`` → ``"0.000075"``.
    Returns *None* if *value* is None.
    """
    if value is None:
        return None
    # Normalise via Decimal to avoid float representation noise.
    return str(Decimal(f"{value:.8g}").normalize())


def _per_million_to_per_1k(v: float | None) -> float | None:
    """Convert per-million-token price to per-1K-token price."""
    return v / 1_000.0 if v is not None else None


def _per_token_to_per_1k(v: float | None) -> float | None:
    """Convert per-token price to per-1K-token price."""
    return v * 1_000.0 if v is not None else None


def _rates_from_raw(
    raw: dict[str, float | None],
    convert: Callable[[float | None], float | None],
) -> dict[str, str] | None:
    """Convert a ``{service_kind: raw_price}`` mapping to per-1K-token strings.

    Returns *None* when both input and output prices are absent (a model with
    only a cached-input rate isn't useful on its own).
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
    """Extract pricing from the llm-stats.com detail payload.

    Returns ``{service_kind: unit_price_string}`` in OCS (per-1K-token) units,
    or *None* when the payload carries no pricing fields.
    """
    # llm-stats.com fields are per-million tokens.
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
) -> dict[str, str] | None:
    """Look up *model_id* in LiteLLM's pricing dataset.

    LiteLLM keys are the bare model name (e.g. ``"gpt-4o"``).  Returns
    ``{service_kind: unit_price_string}`` in per-1K-token units, or *None*.
    """
    entry = litellm_data.get(model_id)
    if entry is None:
        return None
    # LiteLLM fields are per-token.
    return _rates_from_raw(
        {
            "llm_input": entry.get("input_cost_per_token"),
            "llm_output": entry.get("output_cost_per_token"),
            "llm_cached_input": entry.get("cache_read_input_token_cost"),
            "llm_cache_write": entry.get("cache_creation_input_token_cost"),
        },
        _per_token_to_per_1k,
    )


def build_pricing_entries(
    model_id: str,
    providers: list[str],
    pricing: dict[str, str],
) -> list[dict]:
    """Build ``llm_pricing.json``-style entries for each provider in *providers*."""
    rules = [{"service_kind": kind, "unit_price": price} for kind, price in pricing.items()]
    return [{"provider_type": provider, "model_name": model_id, "rules": rules} for provider in providers if rules]


# ---------------------------------------------------------------------------
# API fetch
# ---------------------------------------------------------------------------


def _filter_matched_models(updates: dict) -> list[dict]:
    """Return models from *updates* that belong to a tracked OCS organisation."""
    return [
        m
        for m in updates.get("models", [])
        if m.get("organization", {}).get("id") in ORG_TO_OCS_PROVIDERS and m.get("model_type") == "llm"
    ]


def _enrich_model(model: dict, bearer: str) -> dict:
    """Fetch the detail payload for *model* from the zeroeval Stats API.

    Mutates *model* in-place by adding ``details`` (or ``details_error`` on
    failure) and promoting ``context_window`` from the detail payload when the
    top-level entry lacks it.  Returns the mutated dict.
    """
    model_id = model["id"]
    try:
        details = _api_get(f"https://api.zeroeval.com/stats/v1/models/{model_id}", bearer)
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
    """Fetch and enrich candidate models from the zeroeval Stats API."""
    updates_url = f"https://api.zeroeval.com/stats/v1/updates?days={days}&limit=30"
    updates = _api_get(updates_url, bearer)
    return [_enrich_model(m, bearer) for m in _filter_matched_models(updates)]


# ---------------------------------------------------------------------------
# Candidate classification
# ---------------------------------------------------------------------------


@dataclass
class PricingResult:
    """Resolved per-1K-token pricing for a model, and where it came from."""

    rates: dict[str, str] | None
    source: str | None  # "llm_stats" | "litellm" | None

    @property
    def has_pricing(self) -> bool:
        return bool(self.rates)


@dataclass
class Candidate:
    """Wraps one enriched zeroeval model dict and derives the OCS-specific
    facts (mapped providers, registration state) used during classification.
    """

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
        return self.details.get("url") or f"https://llm-stats.com/models/{self.id}"

    def registered_providers(self, registered: dict[str, set[str]]) -> list[str]:
        """OCS providers under which this model is already registered (or deleted)."""
        return [p for p in self.ocs_providers if self.id in registered.get(p, set())]

    def is_fully_registered(self, registered: dict[str, set[str]]) -> bool:
        """True when the model is already known under *every* provider it maps to."""
        regd = self.registered_providers(registered)
        return bool(regd) and len(regd) == len(self.ocs_providers)


def resolve_pricing(candidate: Candidate, litellm_data: dict[str, Any]) -> PricingResult:
    """Resolve per-1K-token pricing, trying llm-stats first then LiteLLM."""
    if not candidate.details_error:
        rates = resolve_pricing_from_llm_stats(candidate.details)
        if rates:
            return PricingResult(rates, "llm_stats")
    rates = resolve_pricing_from_litellm(candidate.id, litellm_data)
    return PricingResult(rates, "litellm" if rates else None)


def build_model_entry(
    candidate: Candidate,
    registered: dict[str, set[str]],
    priced: set[tuple[str, str]],
    pricing: PricingResult,
) -> tuple[dict, list[dict]]:
    """Build the output entry for a new model and its pricing entries.

    *pricing_entries* is non-empty only when *pricing* has rates and at least
    one mapped provider still needs pricing.
    """
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
    """Classify candidates, resolve pricing, and return the structured output."""
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


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Fetch new LLM model candidates and resolve pricing for OCS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bearer-token",
        required=True,
        metavar="TOKEN",
        help="LLM_STATS_BEARER_TOKEN for the zeroeval Stats API.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="Lookback window in days (default: 1, max: 30).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        metavar="PATH",
        help="Root of the OCS repository checkout (default: current directory).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("candidate_models.json"),
        metavar="FILE",
        help="Path for the output JSON (default: candidate_models.json).",
    )
    return parser


def _write_github_output(new_models: list[dict]) -> None:
    """Append CI gate variables to ``$GITHUB_OUTPUT`` when running in CI.

    Writes ``has_models``, ``model_count``, and ``model_ids`` so that the
    calling workflow can gate the update-models job on whether new candidates
    were found.
    """
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if not gh_out:
        return
    new_count = len(new_models)
    with open(gh_out, "a") as f:
        f.write(f"has_models={'true' if new_count else 'false'}\n")
        f.write(f"model_count={new_count}\n")
        model_ids = ",".join(m["id"] for m in new_models)
        f.write(f"model_ids={model_ids}\n")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: fetch model candidates, resolve pricing, and write JSON output."""
    args = _build_arg_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()

    print(f"[fetch-model-updates] repo_root={repo_root}")

    print("  Loading registered models ...")
    registered = load_registered_models(repo_root)
    total_registered = sum(len(v) for v in registered.values())
    print(f"  → {total_registered} model entries across {len(registered)} providers")

    print("  Loading existing pricing ...")
    priced = load_priced_models(repo_root)
    print(f"  → {len(priced)} priced (provider, model) pairs")

    print("  Fetching LiteLLM pricing fallback ...")
    try:
        litellm_data: dict[str, Any] = _get_json(LITELLM_PRICING_URL)
        print(f"  → {len(litellm_data)} LiteLLM entries")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ Could not fetch LiteLLM pricing ({e}); fallback disabled.")
        litellm_data = {}

    print(f"  Fetching candidates from zeroeval API (days={args.days}) ...")
    candidates = fetch_candidates(args.bearer_token, args.days)
    print(f"  → {len(candidates)} candidate(s) matched to OCS orgs")

    result = process_candidates(candidates, registered, priced, litellm_data)

    output = {
        "run_date": datetime.datetime.now(datetime.UTC).isoformat(),
        "days_lookback": args.days,
        "summary": {
            "candidates_from_api": len(candidates),
            "new_models": len(result["new_models"]),
            "already_registered": len(result["already_registered"]),
            "unpriced": len(result["unpriced_models"]),
            "pricing_entries_generated": len(result["pricing_entries"]),
        },
        **result,
    }

    args.output.write_text(json.dumps(output, indent=2))

    print()
    print(f"  Output → {args.output}")
    print(f"  new_models:          {output['summary']['new_models']}")
    print(f"  already_registered:  {output['summary']['already_registered']}")
    print(f"  unpriced:            {output['summary']['unpriced']}")
    print(f"  pricing_entries:     {output['summary']['pricing_entries_generated']}")

    _write_github_output(result["new_models"])


if __name__ == "__main__":
    main()
