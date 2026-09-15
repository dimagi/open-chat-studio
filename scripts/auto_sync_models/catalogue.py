"""Read OCS's own model catalogue out of the repo.

``default_models.py`` is parsed as AST rather than imported: the reconciliation
runs as a bare ``python3 scripts/...`` in CI, with no Django settings loaded.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path

DEFAULT_MODELS_REL_PATH = "apps/service_providers/llm_service/default_models.py"
IGNORED_MODELS_REL_PATH = "scripts/auto_sync_models/ignored_models.json"


def _module_assignments(repo_root: Path) -> dict[str, ast.expr]:
    """``{assigned_name: value_node}`` for every top-level assignment."""
    tree = ast.parse((repo_root / DEFAULT_MODELS_REL_PATH).read_text())
    return {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _const_str(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _model_call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call) and node.args:
        return _const_str(node.args[0])
    return None


def _default_model_pairs(node: ast.expr | None) -> Iterator[tuple[str, str]]:
    """``(provider, model)`` for each ``Model(...)`` in DEFAULT_LLM_PROVIDER_MODELS."""
    if not isinstance(node, ast.Dict):
        return
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        provider = _const_str(key_node)
        if provider is None or not isinstance(value_node, ast.List):
            continue
        for name in filter(None, map(_model_call_name, value_node.elts)):
            yield provider, name


def _deleted_model_pairs(node: ast.expr | None) -> Iterator[tuple[str, str]]:
    """``(provider, model)`` from each DELETED_MODELS tuple, ignoring any extras."""
    if not isinstance(node, ast.List):
        return
    for elt in node.elts:
        if not (isinstance(elt, ast.Tuple) and len(elt.elts) >= 2):
            continue
        provider, model = _const_str(elt.elts[0]), _const_str(elt.elts[1])
        if provider is not None and model is not None:
            yield provider, model


def load_registered_models(repo_root: Path) -> dict[str, set[str]]:
    """Parse ``default_models.py`` and return ``{provider: {model_name, ...}}``.

    DELETED_MODELS entries are folded in so re-adding a deleted model is
    flagged for review rather than silently re-registered.
    """
    assignments = _module_assignments(repo_root)
    registered: dict[str, set[str]] = {}
    pairs = [
        *_default_model_pairs(assignments.get("DEFAULT_LLM_PROVIDER_MODELS")),
        *_deleted_model_pairs(assignments.get("DELETED_MODELS")),
    ]
    for provider, model in pairs:
        registered.setdefault(provider, set()).add(model)
    return registered


def load_active_default_models(repo_root: Path) -> set[tuple[str, str]]:
    """``(provider, model)`` pairs in ``DEFAULT_LLM_PROVIDER_MODELS`` only.

    The missing-pricing audit consumes this. DELETED_MODELS are deliberately
    excluded - the audit only flags coverage gaps for *active* OCS models.
    """
    assignments = _module_assignments(repo_root)
    return set(_default_model_pairs(assignments.get("DEFAULT_LLM_PROVIDER_MODELS")))


def load_ignored_models(repo_root: Path) -> dict[str, set[str]]:
    """Models a reviewer looked at and chose not to register, per provider.

    Candidates are selected by state, so this ledger is what retires one: it
    keeps a rejected model out of later runs and leaves the slot to the backlog.
    """
    path = repo_root / IGNORED_MODELS_REL_PATH
    if not path.exists():
        return {}
    ignored: dict[str, set[str]] = {}
    for entry in json.loads(path.read_text()):
        ignored.setdefault(entry["provider_type"], set()).add(entry["model_name"])
    return ignored
