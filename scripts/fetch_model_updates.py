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
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _api_get(url: str, bearer: str) -> Any:
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


def load_registered_models(repo_root: Path) -> dict[str, set[str]]:
    """Parse ``default_models.py`` and return ``{provider: {model_name, ...}}``.

    Also captures entries from DELETED_MODELS so that re-adding a deleted
    model is treated as *known* and flagged for review rather than silently
    re-added as a brand-new model.
    """
    src = (repo_root / DEFAULT_MODELS_REL_PATH).read_text()

    registered: dict[str, set[str]] = {}
    current_provider: str | None = None
    in_deleted_models = False

    for line in src.splitlines():
        # Detect DELETED_MODELS assignment
        if re.search(r"\bDELETED_MODELS\b\s*=", line):
            in_deleted_models = True
            current_provider = None
            continue

        if in_deleted_models:
            # Lines like:  ("azure", "gpt-4"),
            m = re.match(r'\s+\("([a-z_]+)",\s+"([^"]+)"\)', line)
            if m:
                provider, model = m.group(1), m.group(2)
                registered.setdefault(provider, set()).add(model)
            # End of the list
            if re.match(r"^\s*\]", line):
                in_deleted_models = False
            continue

        # Detect top-level provider key:  "openai": [
        m = re.match(r'\s+"([a-z_]+)":\s+\[', line)
        if m:
            current_provider = m.group(1)
            registered.setdefault(current_provider, set())
            continue

        # Detect end of a provider list
        if current_provider and re.match(r"\s+\],", line):
            current_provider = None
            continue

        # Detect Model("name", ...) entry
        if current_provider:
            m = re.match(r'\s+Model\("([^"]+)"', line)
            if m:
                registered[current_provider].add(m.group(1))

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


def fetch_candidates(bearer: str, days: int) -> list[dict]:
    """Fetch and enrich candidate models from the zeroeval Stats API."""
    updates_url = (
        f"https://api.zeroeval.com/stats/v1/updates?days={days}&limit=30"
    )
    updates = _api_get(updates_url, bearer)

    matched = [
        m
        for m in updates.get("models", [])
        if m.get("organization", {}).get("id") in ORG_TO_OCS_PROVIDERS
        and m.get("model_type") == "llm"
    ]

    enriched: list[dict] = []
    for m in matched:
        model_id = m["id"]
        try:
            details = _api_get(
                f"https://api.zeroeval.com/stats/v1/models/{model_id}", bearer
            )
            for field in _NOISY_DETAIL_FIELDS:
                details.pop(field, None)
            m["details"] = details
            # Prefer context_window from the detail endpoint when present.
            if details.get("context_window") and not m.get("context_window"):
                m["context_window"] = details["context_window"]
        except urllib.error.HTTPError as e:
            m["details_error"] = f"HTTP {e.code}: {e.reason}"
        except Exception as e:  # noqa: BLE001
            m["details_error"] = str(e)
        enriched.append(m)

    return enriched


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
        context_window = m.get("context_window")
        details = m.get("details", {})

        # ---- Dedup: skip if already in DEFAULT_LLM_PROVIDER_MODELS for
        # every mapped provider (and DELETED_MODELS).
        registered_providers = [
            p for p in ocs_providers if model_id in registered.get(p, set())
        ]
        if registered_providers and len(registered_providers) == len(ocs_providers):
            already_registered.append(
                {
                    "id": model_id,
                    "org": org,
                    "registered_providers": registered_providers,
                }
            )
            continue

        # ---- Pricing resolution
        pricing: dict[str, str] | None = None
        pricing_source: str | None = None

        if not m.get("details_error"):
            pricing = resolve_pricing_from_llm_stats(details)
            if pricing:
                pricing_source = "llm_stats"

        if pricing is None:
            pricing = resolve_pricing_from_litellm(model_id, litellm_data)
            if pricing:
                pricing_source = "litellm"

        # ---- Providers that still need pricing entries
        providers_needing_pricing = [
            p for p in ocs_providers if (p, model_id) not in priced
        ]
        already_priced_providers = [
            p for p in ocs_providers if (p, model_id) in priced
        ]

        model_entry: dict = {
            "id": model_id,
            "org": org,
            "context_window": context_window,
            "ocs_providers": ocs_providers,
            "already_registered_providers": registered_providers,
            "already_priced_providers": already_priced_providers,
            "source_url": (
                details.get("url") or f"https://llm-stats.com/models/{model_id}"
            ),
            "sources": details.get("sources", {}),
            "details_error": m.get("details_error"),
        }

        if pricing:
            pricing_entries = build_pricing_entries(
                model_id, providers_needing_pricing, pricing
            )
            model_entry["pricing"] = {
                "has_pricing": True,
                "source": pricing_source,
                "unit": "per_1k_tokens",
                "rates": pricing,
                "llm_pricing_entries": pricing_entries,
            }
            all_pricing_entries.extend(pricing_entries)
        else:
            model_entry["pricing"] = {
                "has_pricing": False,
                "source": None,
                "reason": "No pricing data found in llm_stats or LiteLLM",
            }
            unpriced.append(
                {
                    "id": model_id,
                    "org": org,
                    "ocs_providers": ocs_providers,
                    "reason": "No pricing data found in llm_stats or LiteLLM",
                }
            )

        new_models.append(model_entry)

    return {
        "new_models": new_models,
        "already_registered": already_registered,
        "unpriced_models": unpriced,
        "pricing_entries": all_pricing_entries,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
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
    args = parser.parse_args(argv)

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

    # Write GITHUB_OUTPUT when running in CI
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        new_count = output["summary"]["new_models"]
        with open(gh_out, "a") as f:
            f.write(f"has_models={'true' if new_count else 'false'}\n")
            f.write(f"model_count={new_count}\n")
            model_ids = ",".join(m["id"] for m in result["new_models"])
            f.write(f"model_ids={model_ids}\n")


if __name__ == "__main__":
    main()
