"""Backfill `seed_data/llm_pricing.json` from LiteLLM for every globally-registered,
non-deprecated model in `DEFAULT_LLM_PROVIDER_MODELS`.

Idempotent: existing entries are preserved verbatim; only the gap is filled.
Models LiteLLM has no entry for are reported and left to the operator to
add manually.

Usage:
    python manage.py backfill_pricing_seed --dry-run
    python manage.py backfill_pricing_seed
"""

import json
import urllib.request
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from apps.service_providers.llm_service.default_models import DEFAULT_LLM_PROVIDER_MODELS

SEED_PATH = Path(__file__).resolve().parents[2] / "seed_data" / "llm_pricing.json"
LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
LITELLM_TO_SERVICE_KIND = {
    "input_cost_per_token": "llm_input",
    "output_cost_per_token": "llm_output",
    "cache_read_input_token_cost": "llm_cached_input",
    "cache_creation_input_token_cost": "llm_cache_write",
}
PER_1K = 1000


class Command(BaseCommand):
    help = "Backfill the pricing seed from LiteLLM for every globally-registered model."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report gaps without writing.")
        parser.add_argument("--path", default=str(SEED_PATH), help="Override the seed JSON path.")

    def handle(self, *args, dry_run: bool, path: str, **options):
        existing = _read_seed(Path(path))
        covered = {(e["provider_type"], e["model_name"]) for e in existing}
        wanted = _enumerate_global_models()
        gaps = wanted - covered

        if not gaps:
            self.stdout.write("Seed already covers every registered model.")
            return

        self.stdout.write(f"Found {len(gaps)} gap(s). Fetching LiteLLM ...")
        litellm = _fetch_litellm()
        new_entries, unpriced = _resolve_gaps(gaps, litellm)

        self.stdout.write(f"Resolved pricing for {len(new_entries)} entr(ies); {len(unpriced)} unpriced.")
        for provider, model in sorted(unpriced):
            self.stdout.write(f"  unpriced: {provider}/{model}")

        if dry_run or not new_entries:
            return

        merged = existing + new_entries
        Path(path).write_text(json.dumps(merged, indent=2) + "\n")
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(new_entries)} new entr(ies) to {path}."))


def _read_seed(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _enumerate_global_models() -> set[tuple[str, str]]:
    """Set of (provider_type, model_name) for every non-deprecated entry in
    `DEFAULT_LLM_PROVIDER_MODELS`. Deprecated models are skipped because
    `test_seed_coverage` only asserts coverage for `deprecated=False`.
    """
    pairs: set[tuple[str, str]] = set()
    for provider_type, models in DEFAULT_LLM_PROVIDER_MODELS.items():
        for model in models:
            if not model.deprecated:
                pairs.add((provider_type, model.name))
    return pairs


def _fetch_litellm() -> dict[str, Any]:
    with urllib.request.urlopen(LITELLM_URL, timeout=30) as resp:
        return json.load(resp)


def _resolve_gaps(
    gaps: set[tuple[str, str]],
    litellm: dict[str, Any],
) -> tuple[list[dict], set[tuple[str, str]]]:
    """Map each (provider, model) gap to a seed entry, or return as unpriced
    if LiteLLM has no row for the model. Provider-prefixed LiteLLM keys
    (e.g. `azure/gpt-4o`) are tried before bare keys to handle Azure-specific
    pricing differences from OpenAI.
    """
    new_entries: list[dict] = []
    unpriced: set[tuple[str, str]] = set()
    for provider, model in sorted(gaps):
        rates = _rates_from_litellm(provider, model, litellm)
        if rates:
            new_entries.append(
                {
                    "provider_type": provider,
                    "model_name": model,
                    "rules": [{"service_kind": k, "unit_price": v} for k, v in rates.items()],
                }
            )
        else:
            unpriced.add((provider, model))
    return new_entries, unpriced


def _rates_from_litellm(provider: str, model: str, litellm: dict[str, Any]) -> dict[str, str]:
    """Look up `model` in LiteLLM, preferring provider-prefixed keys.

    LiteLLM stores per-token costs; we convert to per-1K-tokens for OCS.
    Empty result means no usable rates (no entry, or entry has no costs).
    """
    entry = litellm.get(f"{provider}/{model}") or litellm.get(model)
    if not entry:
        return {}
    rates: dict[str, str] = {}
    for litellm_key, service_kind in LITELLM_TO_SERVICE_KIND.items():
        raw = entry.get(litellm_key)
        if raw is None:
            continue
        rates[service_kind] = _format_per_1k(raw)
    return rates


def _format_per_1k(per_token: float) -> str:
    """Convert per-token cost to per-1K-tokens string with enough precision
    for sub-cent rates (e.g. cached input at $0.0000375 per 1K)."""
    return f"{per_token * PER_1K:.8f}".rstrip("0").rstrip(".") or "0"
