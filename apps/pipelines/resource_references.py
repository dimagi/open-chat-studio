"""Validation and normalization for resource references carried by pipeline node writes."""

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ValidationError

from apps.pipelines.node_type import NodeType
from apps.pipelines.nodes.base import OptionsSource
from apps.utils.fields import as_int

# These are the resource keys that the editor exposes. They intentionally remain the editor's
# wire names; the database representation is handled by ``Node``.
RESOURCE_PARAM_SOURCES = {
    "llm_provider_id": OptionsSource.llm_provider_id,
    "llm_provider_model_id": OptionsSource.llm_provider_model_id,
    "source_material_id": OptionsSource.source_material,
    "collection_id": OptionsSource.collection,
    "collection_index_ids": OptionsSource.collection_index,
    "synthetic_voice_id": OptionsSource.synthetic_voice_id,
    "custom_actions": OptionsSource.custom_actions,
}

UNSET = (None, [], "")


def split_resource_params(params: Mapping[str, Any], resource_param_names: frozenset[str]) -> tuple[dict, dict]:
    """Separate ordinary configuration from resource references without mutating ``params``."""
    ordinary = {name: value for name, value in params.items() if name not in resource_param_names}
    resources = {name: value for name, value in params.items() if name in resource_param_names}
    return ordinary, resources


def validate_resource_params(team, node_type: NodeType, params: Mapping[str, Any]) -> None:
    """Reject resource references that the pipeline's team cannot use.

    The option resolvers are shared with the discovery API, so team ownership, resource type and
    custom-action operation checks cannot drift between the API and the full-graph editor.
    """
    errors = {}
    for param_name, source in RESOURCE_PARAM_SOURCES.items():
        if param_name not in params or params[param_name] in UNSET:
            continue
        if not node_type.declares(param_name):
            # A full editor payload includes relation values rendered by an earlier node type.
            # They are cleared when the type changes, rather than treated as a new reference.
            continue
        requested = _requested_values(source, params[param_name])
        allowed = source.get_resolver()(team, requested)
        invalid = [value for value in requested if not _is_allowed(value, allowed)]
        if invalid:
            errors[param_name] = f"Resource reference is not available to team: {invalid!r}."
    if errors:
        raise ValidationError({"params": errors})


def _requested_values(source: OptionsSource, value: Any) -> list:
    """Return the values a resolver should check for one resource parameter."""
    if source in (OptionsSource.collection_index, OptionsSource.custom_actions):
        return value if isinstance(value, list | tuple | set) else [value]
    return [value]


def _is_allowed(value: Any, allowed: set) -> bool:
    """Match resolver results using the same integer rules as the API reference checker."""
    if isinstance(value, bool | float):
        return False
    try:
        if value in allowed:
            return True
    except TypeError:
        return False
    parsed = as_int(value)
    return parsed is not None and parsed in allowed
