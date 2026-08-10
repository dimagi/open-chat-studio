"""Reshaping the builder's node schemas into what an agent reads.

Everything here is API-side: ``apps.pipelines.node_options`` keeps serving the builder its own
vocabulary untouched.
"""

import hashlib
import json
from functools import cache

from django.conf import settings
from rest_framework.exceptions import NotFound

from apps.pipelines.node_options import get_node_schemas
from apps.pipelines.nodes.base import PipelineRouterNode, resolve_node_class

from .contract import (
    HIDDEN_OPTION_KEYS,
    IMPLIED_OPTION_KEYS,
    MUST_MATCH,
    OPTIONS_KEY_RENAMES,
    OPTIONS_KEYED_BY,
    PER_KEYWORD_OUTPUT,
    SINGLE_OUTPUT,
    UI_KEY_TRANSLATIONS,
)


def _output_topology(schema: dict) -> dict:
    """How edges leave this node type.

    Read from the node class rather than inferred from the schema: "has a `keywords` param" happens
    to identify today's routers but is not what makes a node one. Every listed type is addable, and
    the only terminating type (``EndNode``) is not, so there is no zero-output case to handle.
    """
    node_class = resolve_node_class(schema["title"])
    if node_class is not None and issubclass(node_class, PipelineRouterNode):
        return PER_KEYWORD_OUTPUT
    return SINGLE_OUTPUT


def _agent_property(name: str, prop: dict) -> dict:
    """One node param, in agent vocabulary: `ui:` keys translated or dropped, links made explicit."""
    translated = {
        UI_KEY_TRANSLATIONS[key]: value
        for key, value in prop.items()
        if key in UI_KEY_TRANSLATIONS and value is not None
    }
    plain = {key: value for key, value in prop.items() if not key.startswith("ui:")}
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

    ``ui:documentation_link`` is a site-relative path that the builder joins to
    ``window.DOCUMENTATION_BASE_URL`` in the browser (see ``getDocumentationLink`` in
    assets/javascript/apps/pipeline/utils.tsx). An API client has no such base, so the join happens
    here.
    """
    link = schema.get("ui:documentation_link")
    if not link:
        return None
    if link.startswith("http"):
        return link
    return f"{settings.DOCUMENTATION_BASE_URL}{link}"


def _addable_schemas() -> list[dict]:
    """The builder schemas behind the listed node types.

    ``ui:can_add`` covers both the deprecated types and the structural ones the server manages (the
    deprecation decorator forces it False). The endpoint answers "what can I build", so a type that
    fails that question is not an entry with a flag on it -- it is absent, and ``unknown_node_type``
    explains why if the agent asks directly.
    """
    return [schema for schema in get_node_schemas() if schema.get("ui:can_add")]


@cache
def get_node_types() -> list[dict]:
    """Node types reshaped for agent consumption.

    Cached because the node classes are fixed at import time, so this is static per deploy. The
    cache also captures ``DOCUMENTATION_BASE_URL``, which is deployment-static for the same reason;
    a test that overrides it needs ``get_node_types.cache_clear()``.
    """
    node_types = []
    for schema in _addable_schemas():
        entry = {
            "type": schema["title"],
            "description": schema["description"],
            "outputs": _output_topology(schema),
            "schema": {
                key: value for key, value in schema.items() if not key.startswith("ui:") and key != "properties"
            },
        }
        entry["schema"]["properties"] = {
            name: _agent_property(name, prop) for name, prop in schema["properties"].items()
        }
        if documentation_url := _documentation_url(schema):
            entry["documentation_url"] = documentation_url
        node_types.append(entry)
    return node_types


@cache
def _option_keys_by_type() -> dict[str, frozenset[str]]:
    """The `/pipeline/options/` keys each node type's params can draw from.

    The API does not emit this link per param -- an option key is named for the param that reads it
    -- but `?node_type=` still needs it to trim the payload to one node, and the builder's
    ``ui:optionsSource`` is where the pairing is recorded. A known type that reads nothing yields an
    empty set, which is a different answer from a missing key.
    """
    keys_by_type = {}
    for schema in _addable_schemas():
        properties = schema["properties"]
        keys = set()
        for name, prop in properties.items():
            source = prop.get("ui:optionsSource") or (name if name in IMPLIED_OPTION_KEYS else None)
            if source and source not in HIDDEN_OPTION_KEYS:
                keys.add(OPTIONS_KEY_RENAMES.get(source, source))
        if "llm_provider_id" in properties:
            keys.add("default_llm_provider")
        keys_by_type[schema["title"]] = frozenset(keys)
    return keys_by_type


def option_keys_for_node_type(node_type: str) -> frozenset[str] | None:
    """The option keys a single node type reads, or ``None`` if no such type is served."""
    return _option_keys_by_type().get(node_type)


@cache
def _deprecation_messages() -> dict[str, str]:
    """Replacement advice per deprecated type, so a 404 on one can say more than "unknown"."""
    return {
        schema["title"]: schema.get("ui:deprecation_message", "")
        for schema in get_node_schemas()
        if schema.get("ui:deprecated")
    }


@cache
def _structural_types() -> frozenset[str]:
    """Types the server creates and manages: ``StartNode``, ``EndNode``, ``Passthrough``.

    Unlisted, but ``/inspect/`` still reports them as the ``type`` of real nodes, so a lookup on one
    is a reasonable thing for an agent to do and must not come back as "unknown".
    """
    return frozenset(
        schema["title"]
        for schema in get_node_schemas()
        if not schema.get("ui:can_add") and not schema.get("ui:deprecated")
    )


def _valid_type_names() -> list[str]:
    return [node["type"] for node in get_node_types()]


def unknown_node_type(requested_type: str) -> NotFound:
    """A 404 the agent can act on: why the name failed, and what it could have asked for instead."""
    if (message := _deprecation_messages().get(requested_type)) is not None:
        advice = f" {message}" if message else ""
        detail = f"Node type '{requested_type}' is deprecated and can no longer be used.{advice}"
    elif requested_type in _structural_types():
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
