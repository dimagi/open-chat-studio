"""Layers 1, 2 and 4: read OCS's own view of its models out of the repo.

``default_models.py`` is parsed as AST rather than imported: the reconciliation
runs as a bare ``python3 -m scripts...`` in CI, with no Django settings loaded.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path

from .records import PENDING, Catalogue, Key, LedgerEntry, ModelRecord

DEFAULT_MODELS_REL_PATH = "apps/service_providers/llm_service/default_models.py"
LLM_PRICING_REL_PATH = "apps/cost_tracking/seed_data/llm_pricing.json"
LEDGER_REL_PATH = "scripts/auto_sync_models/model_ledger.json"

# default_models.py writes token limits as ``k(200)`` as often as ``200000``.
KIBI = 1024


# Layer 1: the model catalogue


def read_default_models(repo_root: Path) -> tuple[Catalogue, set[Key]]:
    """``(catalogue, deleted)`` parsed from ``default_models.py``.

    ``DELETED_MODELS`` is kept apart from the catalogue: those models are gone,
    but a model OCS deliberately removed must not be offered back as new.
    """
    assignments = _module_assignments(repo_root / DEFAULT_MODELS_REL_PATH)
    catalogue = {record.key: record for record in _model_records(assignments.get("DEFAULT_LLM_PROVIDER_MODELS"))}
    return catalogue, set(_deleted_keys(assignments.get("DELETED_MODELS")))


def _module_assignments(path: Path) -> dict[str, ast.expr]:
    """``{assigned_name: value_node}`` for every top-level assignment."""
    tree = ast.parse(path.read_text())
    return {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _model_records(node: ast.expr | None) -> Iterator[ModelRecord]:
    if not isinstance(node, ast.Dict):
        return
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        provider = _const_str(key_node)
        if provider is None or not isinstance(value_node, ast.List):
            continue
        for element in value_node.elts:
            record = _model_record(provider, element)
            if record is not None:
                yield record


def _model_record(provider: str, node: ast.expr) -> ModelRecord | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None
    name = _const_str(node.args[0])
    if name is None:
        return None
    keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
    return ModelRecord(
        provider=provider,
        name=name,
        token_limit=_token_limit(node.args[1]) if len(node.args) > 1 else _token_limit(keywords.get("token_limit")),
        deprecated=_const_bool(keywords.get("deprecated")),
        replacement=_const_str(keywords.get("replacement")),
    )


def _token_limit(node: ast.expr | None) -> int | None:
    """Read a literal limit, or the ``k(n)`` helper default_models.py uses for KiB."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "k" and node.args:
        multiplicand = _token_limit(node.args[0])
        return multiplicand * KIBI if multiplicand is not None else None
    return None


def _deleted_keys(node: ast.expr | None) -> Iterator[Key]:
    """``(provider, model)`` from each DELETED_MODELS tuple, ignoring any extras."""
    if not isinstance(node, ast.List):
        return
    for element in node.elts:
        if not (isinstance(element, ast.Tuple) and len(element.elts) >= 2):
            continue
        provider, model = _const_str(element.elts[0]), _const_str(element.elts[1])
        if provider is not None and model is not None:
            yield provider, model


def _const_str(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _const_bool(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


# Layer 2: pricing enrichment


def load_seed(repo_root: Path) -> list[dict]:
    return json.loads((repo_root / LLM_PRICING_REL_PATH).read_text())


def with_pricing(catalogue: Catalogue, seed: list[dict]) -> tuple[Catalogue, list[dict]]:
    """Fold seed rates onto the catalogue; return it with the rows that matched nothing.

    A seed row without a catalogue entry is not an error. ``DELETED_MODELS``
    entries keep their prices so historical usage stays costable, so the orphans
    are handed back to be written out untouched rather than dropped.
    """
    rates_by_key = {(row["provider_type"], row["model_name"]): _row_rates(row) for row in seed}
    priced = {key: record.with_rates(rates_by_key.get(key, {})) for key, record in catalogue.items()}
    orphans = [row for row in seed if (row["provider_type"], row["model_name"]) not in catalogue]
    return priced, orphans


def _row_rates(row: dict) -> dict[str, str]:
    return {rule["service_kind"]: rule["unit_price"] for rule in row["rules"]}


# Layer 4: the ledger


def read_ledger(repo_root: Path) -> dict[Key, LedgerEntry]:
    """Every model the pipeline has already offered, and what was decided.

    The pipeline's only memory. Absent on a first run, which is not an error.
    """
    path = repo_root / LEDGER_REL_PATH
    if not path.exists():
        return {}
    entries = {}
    for raw_key, value in json.loads(path.read_text()).items():
        provider, _, model = raw_key.partition("/")
        entry = LedgerEntry(
            provider=provider,
            model=model,
            first_seen=value.get("first_seen", ""),
            verdict=value.get("verdict", PENDING),
            reason=value.get("reason"),
        )
        entries[entry.key] = entry
    return entries


def write_ledger(repo_root: Path, ledger: dict[Key, LedgerEntry]) -> Path:
    path = repo_root / LEDGER_REL_PATH
    payload = {
        f"{entry.provider}/{entry.model}": _ledger_value(entry)
        for entry in sorted(ledger.values(), key=lambda e: e.key)
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def _ledger_value(entry: LedgerEntry) -> dict:
    value = {"first_seen": entry.first_seen, "verdict": entry.verdict}
    if entry.reason:
        value["reason"] = entry.reason
    return value
