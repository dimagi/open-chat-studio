#!/usr/bin/env python3
"""Diff `apps/cost_tracking/seed_data/llm_pricing.json` against current
llm-stats.com pricing and, if rates have moved, update the seed and emit a
new `load_pricing_data()` data migration. The auto-update-models workflow
runs this daily and opens a "Pricing update" PR when changes are detected.

The seed loader (`load_ai_pricing`) handles the supersession itself: a rule
whose active price matches the seed is a no-op; a rule with a different
price gets the old row closed (`effective_to=now()`) and a fresh active row
inserted. So the generated migration is just a `load_pricing_data()` op.

Usage (from the repo root)::

    python scripts/diff_seed_pricing.py \\
        --bearer-token "$LLM_STATS_BEARER_TOKEN" \\
        --output pricing_changes.json

Outputs:
    pricing_changes.json - structured diff (has_changes, changes, unmatched).
    Updates seed_data/llm_pricing.json in place if has_changes.
    Creates apps/cost_tracking/migrations/NNNN_rate_update_YYYYMMDD.py if has_changes.
    Appends has_changes, change_count, pr_title, pr_body_path to $GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LLM_PRICING_REL_PATH = "apps/cost_tracking/seed_data/llm_pricing.json"
MIGRATIONS_DIR_REL_PATH = "apps/cost_tracking/migrations"
LLM_STATS_DETAIL_URL = "https://api.zeroeval.com/stats/v1/models/{model_id}"
LLM_STATS_PUBLIC_URL = "https://llm-stats.com/models/{model_id}"

# llm-stats.com per-million-token fields → OCS service_kind.
DETAIL_PRICE_FIELDS = {
    "input_price": "llm_input",
    "output_price": "llm_output",
    "cached_input_price": "llm_cached_input",
    "cache_write_price": "llm_cache_write",
}

# Provider keys we diff against llm-stats. llm-stats prices an upstream
# model once (e.g. "gpt-4o"); we apply the same rate to OCS providers that
# wrap that upstream (e.g. openai and azure both consume gpt-4o pricing).
# Other providers (groq, deepseek, etc.) are left to manual updates.
DIFFABLE_PROVIDERS = frozenset({"openai", "azure", "anthropic", "google", "google_vertex_ai"})


@dataclass(frozen=True)
class RateChange:
    """One rate change row for the PR body / structured output."""

    provider_type: str
    model_name: str
    service_kind: str
    old_price: str | None
    new_price: str
    source_url: str


# Seed parsing


def load_seed(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def seed_index(seed: list[dict]) -> dict[tuple[str, str], dict[str, str]]:
    """`{(provider_type, model_name): {service_kind: unit_price_string}}`."""
    index: dict[tuple[str, str], dict[str, str]] = {}
    for entry in seed:
        key = (entry["provider_type"], entry["model_name"])
        index[key] = {r["service_kind"]: r["unit_price"] for r in entry["rules"]}
    return index


def diffable_models(index: dict[tuple[str, str], dict[str, str]]) -> set[str]:
    """Unique `model_name`s in the seed whose providers we diff against
    llm-stats. Models behind non-diffable providers (groq, deepseek, ...)
    are skipped - they have no upstream llm-stats source.
    """
    return {model for (provider, model), _ in index.items() if provider in DIFFABLE_PROVIDERS}


# llm-stats fetch + rate extraction


def _api_get(url: str, bearer: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {bearer}",
            "User-Agent": "ocs-rate-diff-script/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_detail(model_id: str, bearer: str) -> dict | None:
    """Return the llm-stats detail payload for `model_id`, or None on 404."""
    try:
        return _api_get(LLM_STATS_DETAIL_URL.format(model_id=model_id), bearer)
    except urllib.request.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def rates_from_detail(detail: dict) -> dict[str, str]:
    """Per-1K-token rates from an llm-stats detail payload. llm-stats stores
    prices per million tokens; divide by 1000 for per-1K.
    """
    rates: dict[str, str] = {}
    for detail_key, service_kind in DETAIL_PRICE_FIELDS.items():
        raw = detail.get(detail_key)
        if raw is None:
            continue
        rates[service_kind] = _format_per_1k(raw)
    return rates


def _format_per_1k(per_million: float) -> str:
    return f"{per_million / 1000:.8f}".rstrip("0").rstrip(".") or "0"


# Diff


def compute_changes(
    index: dict[tuple[str, str], dict[str, str]],
    fetcher: Callable[[str], dict | None],
) -> tuple[list[RateChange], set[str]]:
    """For each diffable model in `index`, call `fetcher(model_name)` to get
    the upstream rates, then yield a RateChange for each (provider, service_kind)
    whose price differs. `unmatched` returns models the fetcher had no data for.
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
        changes.extend(_changes_for_model(index, model_name, new_rates, source_url))
    return changes, unmatched


def _changes_for_model(
    index: dict[tuple[str, str], dict[str, str]],
    model_name: str,
    new_rates: dict[str, str],
    source_url: str,
) -> list[RateChange]:
    """Compare each diffable provider's seed rate to the new rate, model by model."""
    out: list[RateChange] = []
    for provider, seed_rates in _diffable_provider_rates(index, model_name):
        out.extend(_provider_rate_changes(provider, model_name, seed_rates, new_rates, source_url))
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
    model_name: str,
    seed_rates: dict[str, str],
    new_rates: dict[str, str],
    source_url: str,
) -> list[RateChange]:
    return [
        RateChange(
            provider_type=provider,
            model_name=model_name,
            service_kind=service_kind,
            old_price=seed_rates.get(service_kind),
            new_price=new_price,
            source_url=source_url,
        )
        for service_kind, new_price in new_rates.items()
        if seed_rates.get(service_kind) != new_price
    ]


# Apply changes to seed + generate migration


def apply_changes(seed: list[dict], changes: list[RateChange]) -> list[dict]:
    """Return a new seed list with the changes applied. Entries whose
    (provider, model_name) appears in `changes` get their matching rules
    replaced; everything else is preserved verbatim.
    """
    by_key = {(c.provider_type, c.model_name): {} for c in changes}
    for c in changes:
        by_key[(c.provider_type, c.model_name)][c.service_kind] = c.new_price
    return [_apply_to_entry(entry, by_key) for entry in seed]


def _apply_to_entry(entry: dict, updates_by_key: dict[tuple[str, str], dict[str, str]]) -> dict:
    key = (entry["provider_type"], entry["model_name"])
    if key not in updates_by_key:
        return entry
    updated_kinds = updates_by_key[key]
    new_rules = [_apply_to_rule(rule, updated_kinds) for rule in entry["rules"]]
    return {"provider_type": entry["provider_type"], "model_name": entry["model_name"], "rules": new_rules}


def _apply_to_rule(rule: dict, updated_kinds: dict[str, str]) -> dict:
    return {
        "service_kind": rule["service_kind"],
        "unit_price": updated_kinds.get(rule["service_kind"], rule["unit_price"]),
    }


def generate_migration(migrations_dir: Path, today: datetime.date) -> Path:
    """Write a new `NNNN_rate_update_YYYYMMDD.py` migration that calls
    `load_pricing_data()`. Returns the path written.
    """
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


# PR body


def render_pr_body(changes: list[RateChange], unmatched: set[str]) -> str:
    """Markdown body for the auto-PR. One row per (provider, model, kind)."""
    lines = [
        "Detected rate changes on llm-stats.com against the in-repo seed.",
        "The data migration loads them on deploy; the seed loader supersedes",
        "each affected `PricingRule` (closes the old row, inserts a fresh one).",
        "",
        "| Provider | Model | Service | Old (per 1K) | New (per 1K) | Source |",
        "| --- | --- | --- | --- | --- | --- |",
        *(_change_row(c) for c in changes),
        *_unmatched_section(unmatched),
    ]
    return "\n".join(lines) + "\n"


def _change_row(c: RateChange) -> str:
    old = c.old_price if c.old_price is not None else "-"
    return (
        f"| {c.provider_type} | {c.model_name} | {c.service_kind} | "
        f"{old} | {c.new_price} | [llm-stats]({c.source_url}) |"
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


# CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--bearer-token", required=True)
    parser.add_argument("--repo-root", default=".", type=Path)
    parser.add_argument("--output", default="pricing_changes.json", type=Path)
    parser.add_argument("--today", help="YYYY-MM-DD override (for tests).")
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root.resolve()
    seed_path = repo_root / LLM_PRICING_REL_PATH
    migrations_dir = repo_root / MIGRATIONS_DIR_REL_PATH

    seed = load_seed(seed_path)
    index = seed_index(seed)

    def fetcher(model_id: str) -> dict | None:
        return fetch_detail(model_id, args.bearer_token)

    changes, unmatched = compute_changes(index, fetcher)

    payload = {
        "run_date": datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="seconds"),
        "has_changes": bool(changes),
        "change_count": len(changes),
        "changes": [c.__dict__ for c in changes],
        "unmatched": sorted(unmatched),
    }
    args.output.write_text(json.dumps(payload, indent=2))

    if not changes:
        print("No rate changes detected.")
        _emit_github_output(has_changes=False, change_count=0, pr_title="", pr_body_path="")
        return 0

    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    seed_path.write_text(json.dumps(apply_changes(seed, changes), indent=2) + "\n")
    migration_path = generate_migration(migrations_dir, today)

    body_path = args.output.with_suffix(".body.md")
    body_path.write_text(render_pr_body(changes, unmatched))

    print(f"Detected {len(changes)} rate change(s). Migration: {migration_path.name}")
    _emit_github_output(
        has_changes=True,
        change_count=len(changes),
        pr_title=f"Pricing update: {len(changes)} rate change(s) ({today.isoformat()})",
        pr_body_path=str(body_path),
    )
    return 0


def _emit_github_output(*, has_changes: bool, change_count: int, pr_title: str, pr_body_path: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as f:
        f.write(f"has_changes={'true' if has_changes else 'false'}\n")
        f.write(f"change_count={change_count}\n")
        f.write(f"pr_title={pr_title}\n")
        f.write(f"pr_body_path={pr_body_path}\n")


if __name__ == "__main__":
    raise SystemExit(main())
