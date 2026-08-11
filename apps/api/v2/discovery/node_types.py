"""Reshapes the builder's node schemas into the payload `/pipeline/nodes/` serves.

Everything here is API-side; ``apps.pipelines.nodes.node_metadata`` keeps serving the builder its own
vocabulary untouched.
"""

import hashlib
import json
from functools import cache

from django.conf import settings
from rest_framework.exceptions import NotFound

from apps.pipelines.nodes.base import PipelineRouterNode, resolve_node_class
from apps.pipelines.nodes.node_metadata import get_node_schemas

from .contract import (
    MUST_MATCH,
    OPTIONS_KEY_RENAMES,
    OPTIONS_KEYED_BY,
    PER_KEYWORD_OUTPUT,
    SINGLE_OUTPUT,
    UI_KEY_TRANSLATIONS,
)


@cache
def get_node_types() -> list[dict]:
    """Node types reshaped for client consumption.

    Static per deploy, so it is memoised. The cache also captures ``DOCUMENTATION_BASE_URL``; a test
    that overrides that setting needs ``get_node_types.cache_clear()``.
    """
    node_types = []
    for schema in _addable_schemas():
        entry = {
            "type": schema["title"],
            "description": schema["description"],
            "outputs": _output_topology(schema),
            "schema": _schema(schema),
        }
        if documentation_url := _documentation_url(schema):
            entry["documentation_url"] = documentation_url
        node_types.append(entry)
    return node_types


def option_keys_for_node_type(node_type: str) -> frozenset[str] | None:
    """The option keys a single node type reads, or ``None`` if no such type is served."""
    return _option_keys_by_type().get(node_type)


def served_option_keys() -> frozenset[str]:
    """Every option key some listed node type can reference, in API vocabulary.

    The unscoped `/pipeline/options/` payload is this set plus ``API_ONLY_OPTION_KEYS``.
    """
    return frozenset().union(*_option_keys_by_type().values())


def unknown_node_type(requested_type: str) -> NotFound:
    """A 404 carrying why the name failed and what the client could have asked for instead.

    A deprecated type reads as unknown -- only the server-managed types get a message of their own.
    """
    if requested_type in _structural_types():
        detail = (
            f"Node type '{requested_type}' is managed by the server and cannot be created or "
            f"configured. It may appear as a node's `type` in /inspect/ responses."
        )
    else:
        detail = f"Unknown node type: {requested_type}"
    return NotFound({"detail": detail, "valid_types": _valid_type_names()})


def etag(payload) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return f'W/"{digest[:32]}"'


def _addable_schemas() -> list[dict]:
    """The builder schemas behind the listed node types.

    ``ui:can_add`` is False for both the deprecated types and the structural ones the server manages.
    """
    return [schema for schema in get_node_schemas() if schema.get("ui:can_add")]


def _output_topology(schema: dict) -> dict:
    """How edges leave this node type, read from the node class.

    Every listed type is addable and the only terminating type (``EndNode``) is not, so there is no
    zero-output case to handle.
    """
    node_class = resolve_node_class(schema["title"])
    if node_class is not None and issubclass(node_class, PipelineRouterNode):
        return PER_KEYWORD_OUTPUT
    return SINGLE_OUTPUT


def _schema(node_schema: dict) -> dict:
    """The node's JSON Schema as served, with the params the API withholds taken out.

    A withheld param leaves ``required`` along with ``properties``.
    """
    served = {key: value for key, value in node_schema.items() if ":" not in key and key != "properties"}
    served["properties"] = {
        name: _property(name, prop) for name, prop in node_schema["properties"].items() if not prop.get("api:exclude")
    }
    if required := served.get("required"):
        served["required"] = [name for name in required if name in served["properties"]]
    return served


def _property(name: str, prop: dict) -> dict:
    """One node param, as served: every `ui:`/`api:` key dropped, the two that carry meaning re-added
    under client names, and the cross-param links attached."""
    translated = {
        UI_KEY_TRANSLATIONS[key]: value
        for key, value in prop.items()
        if key in UI_KEY_TRANSLATIONS and value is not None
    }
    plain = {key: value for key, value in prop.items() if ":" not in key}
    return plain | translated | _param_links(name)


def _param_links(name: str) -> dict:
    """The cross-param rules the builder enforces in JS and the schema never stated."""
    links = {}
    if name in MUST_MATCH:
        links["must_match"] = MUST_MATCH[name]
    if name in OPTIONS_KEYED_BY:
        links["options_keyed_by"] = OPTIONS_KEYED_BY[name]
    return links


def _documentation_url(schema: dict) -> str | None:
    """The node's help link, absolutised.

    ``ui:documentation_link`` is a site-relative path the builder joins to
    ``window.DOCUMENTATION_BASE_URL`` in the browser (``getDocumentationLink`` in
    assets/javascript/apps/pipeline/utils.tsx); an API client has no such base.
    """
    link = schema.get("ui:documentation_link")
    if not link:
        return None
    if link.startswith("http"):
        return link
    return f"{settings.DOCUMENTATION_BASE_URL}{link}"


@cache
def _option_keys_by_type() -> dict[str, frozenset[str]]:
    """The `/pipeline/options/` keys each node type's params can draw from, read off
    ``ui:optionsSource``.

    Withheld params contribute nothing. A known type that reads nothing yields an empty set, which is
    a different answer from a missing key.
    """
    keys_by_type = {}
    for schema in _addable_schemas():
        properties = schema["properties"]
        keys = set()
        for prop in properties.values():
            if prop.get("api:exclude"):
                continue
            if source := prop.get("ui:optionsSource"):
                keys.add(OPTIONS_KEY_RENAMES.get(source, source))
        if "llm_provider_id" in properties:
            keys.add("default_llm_provider")
        keys_by_type[schema["title"]] = frozenset(keys)
    return keys_by_type


@cache
def _structural_types() -> frozenset[str]:
    """Types the server creates and manages: ``StartNode``, ``EndNode``, ``Passthrough``.

    Unlisted, but ``/inspect/`` still reports them as the ``type`` of real nodes.
    """
    return frozenset(
        schema["title"]
        for schema in get_node_schemas()
        if not schema.get("ui:can_add") and not schema.get("ui:deprecated")
    )


def _valid_type_names() -> list[str]:
    return [node["type"] for node in get_node_types()]
