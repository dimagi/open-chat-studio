"""Layer 6: turn one comparison into files, bodies and workflow gate variables.

Two kinds of output. The pricing edits are mechanical, so they are written here
and the workflow opens a PR from them directly. Everything needing a judgement
call -- which providers really serve a new model, which parameters class it
wants, whether a deprecation has a successor -- goes into ``reconciliation.json``
for the Claude Code job.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from .catalogue import LEDGER_REL_PATH, load_seed, write_ledger
from .records import PENDING, Diff, Key, LedgerEntry, ModelRecord, RateChange, Reconciliation
from .upstream import SOURCE_URL

LLM_PRICING_REL_PATH = "apps/cost_tracking/seed_data/llm_pricing.json"


def dispatch(
    reconciliation: Reconciliation,
    repo_root: Path,
    output: Path,
    dry_run: bool = False,
) -> None:
    """Write everything this run can derive on its own.

    The seed rewrite, the payload and the ledger are mechanical. The migration
    that loads the seed is not: it belongs in the same file as the model-list
    migration the catalogue work needs, which only a reader of
    ``docs/developer_guides/managing_models.md`` can write.
    """
    diff = reconciliation.diff
    _write_pricing_update(diff, repo_root, output, dry_run)
    if diff.added and not dry_run:
        advanced = advance_ledger(reconciliation.ledger, diff.added, reconciliation.today)
        path = write_ledger(repo_root, advanced)
        print(f"  -> recorded {len(diff.added)} model(s) as pending in {path.name}")

    run_date = datetime.datetime.now(datetime.UTC).isoformat()
    payload = build_payload(diff, reconciliation.orphan_rows, run_date=run_date)
    output.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\n  Output -> {output}")
    for name, value in payload["summary"].items():
        print(f"  {name}: {value}")

    _write_gate(diff)


def _write_gate(diff: Diff) -> None:
    """One variable, so the workflow can skip the agent when there is nothing to do.

    The script owns what counts as work; computing it again in YAML would be a
    second definition to drift from this one.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as handle:
        handle.write(f"has_work={'true' if diff.has_work else 'false'}\n")


# The pricing seed


def _write_pricing_update(diff: Diff, repo_root: Path, output: Path, dry_run: bool) -> None:
    """Rewrite the seed and render the rate table the PR description quotes."""
    if not diff.has_pricing_work:
        return
    if dry_run:
        print(f"  -> dry run: would apply {len(diff.repriced)} rate change(s) and {len(diff.backfilled)} backfill(s)")
        return

    seed_path = repo_root / LLM_PRICING_REL_PATH
    seed_path.write_text(json.dumps(apply_rate_changes(load_seed(repo_root), diff), indent=2) + "\n")
    body_path = output.with_name(output.stem + ".pricing-body.md")
    body_path.write_text(render_pricing_pr_body(diff))
    print(f"  -> updated the seed; rate table in {body_path.name}")


def apply_rate_changes(seed: list[dict], diff: Diff) -> list[dict]:
    """Apply every rate change to the seed rows, in place where a row exists.

    Rows the catalogue knows nothing about are carried through untouched: a
    ``DELETED_MODELS`` entry keeps its price so historical usage stays costable.
    """
    updates: dict[Key, dict[str, str]] = {}
    for change in [*diff.repriced, *diff.backfilled]:
        updates.setdefault(change.key, {})[change.service_kind] = change.new_price

    updated = [_apply_to_row(row, updates) for row in seed]
    seen = {(row["provider_type"], row["model_name"]) for row in seed}
    for key in sorted(updates.keys() - seen):
        provider, model = key
        updated.append({"provider_type": provider, "model_name": model, "rules": _rules(updates[key])})
    return updated


def _apply_to_row(row: dict, updates: dict[Key, dict[str, str]]) -> dict:
    changed = updates.get((row["provider_type"], row["model_name"]))
    if not changed:
        return row
    rates = {rule["service_kind"]: rule["unit_price"] for rule in row["rules"]}
    rates.update(changed)
    return {"provider_type": row["provider_type"], "model_name": row["model_name"], "rules": _rules(rates)}


def _rules(rates: dict[str, str]) -> list[dict]:
    return [{"service_kind": kind, "unit_price": price} for kind, price in rates.items()]


def render_pricing_pr_body(diff: Diff) -> str:
    lines: list[str] = []
    if diff.repriced:
        lines += [
            "Rates that have moved in the LiteLLM price table since the seed was last written.",
            "The data migration loads them on deploy; the seed loader supersedes each affected",
            "`PricingRule` (closes the old row, inserts a fresh one).",
            "",
            "| Provider | Model | Service | Old (per 1K) | New (per 1K) |",
            "| --- | --- | --- | --- | --- |",
            *(
                f"| {c.provider} | {c.model} | {c.service_kind} | {c.old_price} | {c.new_price} |"
                for c in diff.repriced
            ),
        ]
    if diff.backfilled:
        if lines:
            lines.append("")
        lines += [
            "## Backfilled from LiteLLM",
            "",
            "These rates were missing from the seed entirely. Verify the magnitudes before merging.",
            "",
            "| Provider | Model | Service | Price (per 1K) |",
            "| --- | --- | --- | --- |",
            *(f"| {c.provider} | {c.model} | {c.service_kind} | {c.new_price} |" for c in diff.backfilled),
        ]
    if diff.unpriced:
        lines += [
            "",
            "## Still uncostable",
            "",
            "LiteLLM had no usable rate for these, so they are not covered by this PR:",
            *(f"- `{gap.provider}/{gap.model}` (missing {', '.join(gap.kinds_missing)})" for gap in diff.unpriced),
        ]
    lines += ["", f"Source: [LiteLLM price table]({SOURCE_URL})."]
    return "\n".join(lines) + "\n"


# The ledger


def advance_ledger(
    ledger: dict[Key, LedgerEntry],
    added: list[ModelRecord],
    today: datetime.date,
) -> dict[Key, LedgerEntry]:
    """Record each offered model as pending, keeping any verdict already set.

    Only ``rejected`` takes a model out of later runs, so an entry left pending
    is offered again until the Claude Code job decides it.
    """
    advanced = dict(ledger)
    for record in added:
        advanced.setdefault(
            record.key,
            LedgerEntry(
                provider=record.provider,
                model=record.name,
                first_seen=today.isoformat(),
                verdict=PENDING,
                params=record.params,
            ),
        )
    return advanced


# The payload


def build_payload(diff: Diff, orphan_rows: list[dict], run_date: str) -> dict:
    return {
        "run_date": run_date,
        "source_url": SOURCE_URL,
        "ledger_path": LEDGER_REL_PATH,
        "summary": {
            "added": len(diff.added),
            "removed": len(diff.removed),
            "deprecated": len(diff.deprecated),
            "repriced": len(diff.repriced),
            "backfilled": len(diff.backfilled),
            "unpriced": len(diff.unpriced),
            "seed_rows_without_catalogue_entry": len(orphan_rows),
        },
        "added": [_added_entry(record) for record in diff.added],
        "removed": [{"provider": r.provider, "model": r.name} for r in diff.removed],
        "deprecated": [
            {"provider": r.provider, "model": r.name, "deprecation_date": r.deprecation_date} for r in diff.deprecated
        ],
        "repriced": [_rate_entry(c) for c in diff.repriced],
        "backfilled": [_rate_entry(c) for c in diff.backfilled],
        "unpriced": [
            {"provider": g.provider, "model": g.model, "kinds_missing": list(g.kinds_missing)} for g in diff.unpriced
        ],
    }


def _added_entry(record: ModelRecord) -> dict:
    return {
        "provider": record.provider,
        "model": record.name,
        "token_limit": record.token_limit,
        "params": record.params,
        "rates": record.rates,
        "deprecation_date": record.deprecation_date,
        "litellm_key": record.source_key,
        "pricing_entry": {
            "provider_type": record.provider,
            "model_name": record.name,
            "rules": _rules(record.rates),
        }
        if record.rates
        else None,
    }


def _rate_entry(change: RateChange) -> dict:
    return {
        "provider": change.provider,
        "model": change.model,
        "service_kind": change.service_kind,
        "old_price": change.old_price,
        "new_price": change.new_price,
    }
