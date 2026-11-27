"""Normalization of resource references carried by pipeline node writes."""

from collections.abc import Mapping
from typing import Any


def split_resource_params(params: Mapping[str, Any], resource_param_names: frozenset[str]) -> tuple[dict, dict]:
    """Separate ordinary configuration from resource references without mutating ``params``."""
    ordinary = {name: value for name, value in params.items() if name not in resource_param_names}
    resources = {name: value for name, value in params.items() if name in resource_param_names}
    return ordinary, resources
