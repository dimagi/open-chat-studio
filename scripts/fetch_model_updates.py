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
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
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

LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main"
    "/model_prices_and_context_window.json"
)

DEFAULT_MODELS_REL_PATH = "apps/service_providers/llm_service/default_models.py"
LLM_PRICING_REL_PATH = "apps/cost_tracking/seed_data/llm_pricing.json"

# Detail-response fields that add noise without helping the agent.
_NOISY_DETAIL_FIELDS = {"scores", "top_scores", "providers"}

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


def _parse_deleted_models_line(
    line: str,
    registered: dict[str, set[str]],
) -> bool:
    """Parse one line inside the DELETED_MODELS block.

    Handles both 2-tuples ("provider", "model") and 3-tuples
    ("provider", "model", "replacement").  Adds any matched pair to
    *registered* and returns True when the closing ``]`` is encountered
    (signalling the block has ended), False otherwise.
    """
    m = re.match(r'\s+\("([a-z_]+)",\s+"([^"]+)"', line)
    if m:
        registered.setdefault(m.group(1), set()).add(m.group(2))
    return bool(re.match(r"^\s*\]", line))


def _parse_model_name(line: str, next_line: str | None) -> str | None:
    """Extract a model name from a ``Model(...)`` line.

    Handles both single-line (``Model("name", ...)``) and multi-line forms
    where the name appears on the following line.
    """
    m = re.match(r'\s+Model\("([^"]+)"', line)
    if m:
        return m.group(1)
    if re.match(r'\s+Model\(\s*$', line) and next_line is not None:
        name_m = re.match(r'\s+"([^"]+)"', next_line)
        if name_m:
            return name_m.group(1)
    return None


def load_registered_models(repo_root: Path) -> dict[str, set[str]]:
    """Parse ``default_models.py`` and return ``{provider: {model_name, ...}}``.

    Also captures entries from DELETED_MODELS so that re-adding a deleted
    model is treated as *known* and flagged for review rather than silently
    re-added as a brand-new model.
    """
    lines = (repo_root / DEFAULT_MODELS_REL_PATH).read_text().splitlines()
    registered: dict[str, set[str]] = {}
    current_provider: str | None = None
    in_deleted_models = False

    for i, line in enumerate(lines):
        if re.search(r"\bDELETED_MODELS\b\s*=", line):
            in_deleted_models = True
            current_provider = None
            continue

        if in_deleted_models:
            if _parse_deleted_models_line(line, registered):
                in_deleted_models = False
            continue

        provider_m = re.match(r'\s+"([a-z_]+)":\s+\[', line)
        if provider_m:
            current_provider = provider_m.group(1)
            registered.setdefault(current_provider, set())
            continue

        if current_provider and re.match(r"\s+\],", line):
            current_provider = None
            continue

        if current_provider:
            next_line = lines[i + 1] if i + 1 < len(lines) else None
            name = _parse_model_name(line, next_line)
            if name:
                registered[current_provider].add(name)

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


def resolve_pricing_from_llm_stats(
    details: dict[str, Any],
) -> dict[str, str] | None:
    """Extract pricing from the llm-stats.com detail payload.

    Returns ``{service_kind: unit_price_string}`` in OCS (per-1K-token) units,
    or *None* when the payload carries no pricing fields.
    """
    # llm-stats.com fields are per-million tokens.
    raw = {
        "llm_input": details.get("input_price"),
        "llm_output": details.get("output_price"),
        "llm_cached_input": details.get("cached_input_price"),
        "llm_cache_write": details.get("cache_write_price"),
    }

    if raw["llm_input"] is None and raw["llm_output"] is None:
        return None

    result = {
        kind: _fmt(_per_million_to_per_1k(v))
        for kind, v in raw.items()
        if v is not None
    }
    return result or None


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
    raw = {
        "llm_input": entry.get("input_cost_per_token"),
        "llm_output": entry.get("output_cost_per_token"),
        "llm_cached_input": entry.get("cache_read_input_token_cost"),
        "llm_cache_write": entry.get("cache_creation_input_token_cost"),
    }

    if raw["llm_input"] is None and raw["llm_output"] is None:
        return None

    result = {
        kind: _fmt(_per_token_to_per_1k(v))
        for kind, v in raw.items()
        if v is not None
    }
    return result or None


def build_pricing_entries(
    model_id: str,
    providers: list[str],
    pricing: dict[str, str],
) -> list[dict]:
    """Build ``llm_pricing.json``-style entries for each provider in *providers*."""
    rules = [
        {"service_kind": kind, "unit_price": price}
        for kind, price in pricing.items()
    ]
    return [
        {"provider_type": provider, "model_name": model_id, "rules": rules}
        for provider in providers
        if rules
    ]


# ---------------------------------------------------------------------------
# API fetch
# ---------------------------------------------------------------------------


def _filter_matched_models(updates: dict) -> list[dict]:
    """Return models from *updates* that belong to a tracked OCS organisation."""
    return [
        m
        for m in updates.get("models", [])
        if m.get("organization", {}).get("id") in ORG_TO_OCS_PROVIDERS
        and m.get("model_type") == "llm"
    ]


def _enrich_model(model: dict, bearer: str) -> dict:
    """Fetch the detail payload for *model* from the zeroeval Stats API.

    Mutates *model* in-place by adding ``details`` (or ``details_error`` on
    failure) and promoting ``context_window`` from the detail payload when the
    top-level entry lacks it.  Returns the mutated dict.
    """
    model_id = model["id"]
    try:
        details = _api_get(
            f"https://api.zeroeval.com/stats/v1/models/{model_id}", bearer
        )
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
# Core pipeline helpers
# ---------------------------------------------------------------------------


def _get_registered_providers(
    model_id: str,
    ocs_providers: list[str],
    registered: dict[str, set[str]],
) -> list[str]:
    """Return the subset of *ocs_providers* for which *model_id* is already registered (or deleted)."""
    return [p for p in ocs_providers if model_id in registered.get(p, set())]


def _resolve_pricing_with_source(
    model_id: str,
    m: dict,
    litellm_data: dict[str, Any],
) -> tuple[dict[str, str] | None, str | None]:
    """Resolve per-1K-token pricing for *model_id*, trying llm-stats then LiteLLM.

    Returns ``(pricing_dict, source_name)`` where *source_name* is ``"llm_stats"``,
    ``"litellm"``, or *None* when no pricing data is available.
    """
    if not m.get("details_error"):
        pricing = resolve_pricing_from_llm_stats(m.get("details", {}))
        if pricing:
            return pricing, "llm_stats"
    pricing = resolve_pricing_from_litellm(model_id, litellm_data)
    return pricing, ("litellm" if pricing else None)


def _build_model_entry(
    m: dict,
    ocs_providers: list[str],
    registered_providers: list[str],
    priced: set[tuple[str, str]],
    pricing: dict[str, str] | None,
    pricing_source: str | None,
) -> tuple[dict, list[dict]]:
    """Build a model entry dict and its pricing entries list.

    Returns ``(entry, pricing_entries)`` where *pricing_entries* is non-empty
    only when *pricing* is not None.
    """
    model_id = m["id"]
    details = m.get("details", {})
    already_priced = [p for p in ocs_providers if (p, model_id) in priced]

    entry: dict = {
        "id": model_id,
        "org": m.get("organization", {}).get("id", ""),
        "context_window": m.get("context_window"),
        "ocs_providers": ocs_providers,
        "already_registered_providers": registered_providers,
        "already_priced_providers": already_priced,
        "source_url": details.get("url") or f"https://llm-stats.com/models/{model_id}",
        "sources": details.get("sources", {}),
        "details_error": m.get("details_error"),
    }

    if not pricing:
        entry["pricing"] = {
            "has_pricing": False,
            "source": None,
            "reason": "No pricing data found in llm_stats or LiteLLM",
        }
        return entry, []

    providers_needing_pricing = [p for p in ocs_providers if (p, model_id) not in priced]
    p_entries = build_pricing_entries(model_id, providers_needing_pricing, pricing)
    entry["pricing"] = {
        "has_pricing": True,
        "source": pricing_source,
        "unit": "per_1k_tokens",
        "rates": pricing,
        "llm_pricing_entries": p_entries,
    }
    return entry, p_entries


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


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

    for m in candidates:
        model_id = m["id"]
        org = m.get("organization", {}).get("id", "")
        ocs_providers = ORG_TO_OCS_PROVIDERS.get(org, [])
        registered_providers = _get_registered_providers(model_id, ocs_providers, registered)

        if registered_providers and len(registered_providers) == len(ocs_providers):
            already_registered.append(
                {"id": model_id, "org": org, "registered_providers": registered_providers}
            )
            continue

        pricing, pricing_source = _resolve_pricing_with_source(model_id, m, litellm_data)
        entry, p_entries = _build_model_entry(
            m, ocs_providers, registered_providers, priced, pricing, pricing_source
        )
        all_pricing_entries.extend(p_entries)
        new_models.append(entry)

        if not pricing:
            unpriced.append(
                {
                    "id": model_id,
                    "org": org,
                    "ocs_providers": ocs_providers,
                    "reason": "No pricing data found in llm_stats or LiteLLM",
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
        "run_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
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
