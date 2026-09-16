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
from .records import PENDING, Diff, Key, LedgerEntry, ModelRecord, PricingGap, RateChange
from .upstream import SOURCE_URL

LLM_PRICING_REL_PATH = "apps/cost_tracking/seed_data/llm_pricing.json"
MIGRATIONS_DIR_REL_PATH = "apps/cost_tracking/migrations"


def dispatch(
    diff: Diff,
    orphan_rows: list[dict],
    ledger: dict[Key, LedgerEntry],
    repo_root: Path,
    output: Path,
    today: datetime.date,
    dry_run: bool = False,
) -> None:
    """Write every artefact this run produces, then the gate variables."""
    pricing_body_path = _write_pricing_update(diff, repo_root, output, today, dry_run)
    missing_body_path = _write_missing_pricing_issue(diff, output, dry_run)
    if diff.added and not dry_run:
        path = write_ledger(repo_root, advance_ledger(ledger, diff.added, today))
        print(f"  -> recorded {len(diff.added)} model(s) as pending in {path.name}")

    payload = build_payload(diff, orphan_rows, run_date=datetime.datetime.now(datetime.UTC).isoformat())
    output.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\n  Output -> {output}")
    for name, value in payload["summary"].items():
        print(f"  {name}: {value}")

    _write_github_output(
        github_outputs(diff, today=today, pricing_body_path=pricing_body_path, missing_body_path=missing_body_path)
    )


# The pricing PR: seed rewrite + migration + body


def _write_pricing_update(
    diff: Diff, repo_root: Path, output: Path, today: datetime.date, dry_run: bool
) -> Path | None:
    """Rewrite the seed, emit a migration, write the PR body. None when there is nothing to do."""
    if not diff.has_pricing_work:
        return None
    if dry_run:
        print(f"  -> dry run: would apply {len(diff.repriced)} rate change(s) and {len(diff.backfilled)} backfill(s)")
        return None

    seed_path = repo_root / LLM_PRICING_REL_PATH
    seed_path.write_text(json.dumps(apply_rate_changes(load_seed(repo_root), diff), indent=2) + "\n")
    migration_path = generate_migration(repo_root / MIGRATIONS_DIR_REL_PATH, today)
    print(f"  -> updated the seed and wrote {migration_path.name}")

    body_path = output.with_name(output.stem + ".pricing-body.md")
    body_path.write_text(render_pricing_pr_body(diff))
    return body_path


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


def generate_migration(migrations_dir: Path, today: datetime.date) -> Path:
    """Write a rate-update migration depending on the latest existing one."""
    existing = sorted(path.stem for path in migrations_dir.glob("[0-9]*.py"))
    if not existing:
        raise RuntimeError(f"No existing migrations in {migrations_dir}")
    previous = existing[-1]
    number = int(previous.split("_", 1)[0]) + 1
    target = migrations_dir / f"{number:04d}_rate_update_{today.strftime('%Y%m%d')}.py"
    target.write_text(
        "from django.db import migrations\n\n"
        "from apps.cost_tracking.migration_utils import load_pricing_data\n\n\n"
        "class Migration(migrations.Migration):\n"
        f'    dependencies = [("cost_tracking", "{previous}")]\n'
        "    operations = [load_pricing_data()]\n"
    )
    return target


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


def pricing_pr_title(diff: Diff, today: datetime.date) -> str:
    parts = []
    if diff.repriced:
        parts.append(f"{len(diff.repriced)} rate change(s)")
    if diff.backfilled:
        parts.append(f"{len(diff.backfilled)} backfilled")
    return f"Pricing update: {', '.join(parts)} ({today.isoformat()})"


# The missing-pricing issue


def _write_missing_pricing_issue(diff: Diff, output: Path, dry_run: bool) -> Path | None:
    if not diff.unpriced or dry_run:
        return None
    body_path = output.with_name(output.stem + ".missing-pricing-body.md")
    body_path.write_text(render_missing_pricing_issue_body(diff.unpriced))
    return body_path


def render_missing_pricing_issue_body(gaps: list[PricingGap]) -> str:
    lines = [
        "These OCS-registered models have no usable pricing in",
        "`apps/cost_tracking/seed_data/llm_pricing.json`, and LiteLLM has none to",
        "backfill from. The dashboard cannot compute exact costs for their usage",
        "until a seed entry is added by hand.",
        "",
        "| Provider | Model | Missing |",
        "| --- | --- | --- |",
        *(f"| {gap.provider} | {gap.model} | {', '.join(gap.kinds_missing)} |" for gap in gaps),
    ]
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


# The payload and the workflow gates


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


def github_outputs(
    diff: Diff,
    today: datetime.date,
    pricing_body_path: Path | None,
    missing_body_path: Path | None,
) -> list[str]:
    """The gate variables the workflow reads back from ``$GITHUB_OUTPUT``.

    The pricing PR is gated on its body having been written rather than on
    changes being found: ``--dry-run`` finds them and writes nothing.
    """
    values: dict[str, object] = {
        "has_catalogue_work": diff.has_catalogue_work,
        "new_model_count": len(diff.added),
        "new_model_ids": ",".join(f"{r.provider}/{r.name}" for r in diff.added),
        "deprecated_count": len(diff.deprecated),
        "removed_count": len(diff.removed),
        "removed_model_ids": ",".join(f"{r.provider}/{r.name}" for r in diff.removed),
        "has_price_changes": pricing_body_path is not None,
        "price_change_count": len(diff.repriced),
        "backfilled_count": len(diff.backfilled),
        "pricing_pr_title": pricing_pr_title(diff, today) if pricing_body_path else "",
        "pricing_pr_body_path": pricing_body_path,
        "has_missing_pricing": missing_body_path is not None,
        "missing_pricing_count": len(diff.unpriced),
        "missing_pricing_issue_body_path": missing_body_path,
    }
    return [f"{name}={_render(value)}" for name, value in values.items()]


def _render(value: object) -> str:
    """Booleans as Actions expects them, and an absent path as an empty string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _write_github_output(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as handle:
        for line in lines:
            handle.write(f"{line}\n")
