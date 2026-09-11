#!/usr/bin/env python3
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
IGNORED_MODELS_REL_PATH = "scripts/reconcile_ignored_models.json"


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
