"""Reshapes the builder's node schemas (``apps.pipelines.nodes.node_metadata``) into the payload
`/pipeline/nodes/` serves."""

import hashlib
import json
from functools import cache
from typing import cast

from django.conf import settings
from rest_framework.exceptions import NotFound

from apps.pipelines.nodes.base import BasePipelineNode, OptionsSource, PipelineRouterNode, resolve_node_class
from apps.pipelines.nodes.node_metadata import get_node_schemas

from .contract import (
    MUST_MATCH,
    OPTIONS_KEYED_BY,
    PARAMETER_OPTION_SOURCES,
    PER_KEYWORD_OUTPUT,
    SINGLE_OUTPUT,
    UI_KEY_TRANSLATIONS,
)


@cache
def get_node_types() -> list[dict]:
    """Node types reshaped for client consumption. Static per deploy, so it is memoised -- a test
    overriding ``DOCUMENTATION_BASE_URL`` needs ``get_node_types.cache_clear()``."""
    node_types = []
    for schema in _available_schemas():
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


def get_node_type_schema(node_type: str) -> dict:
    """The named node type as ``/pipeline/nodes/`` serves it, or a 404 naming the valid ones."""
    for node in get_node_types():
        if node["type"] == node_type:
            return node
    raise unknown_node_type(node_type)


def get_node_class(node_type: str) -> type[BasePipelineNode]:
    """The node class behind a type this API publishes, or :func:`get_node_type_schema`'s 404.

    The served types are exactly the resolvable node classes, so past the schema lookup this cannot
    come back None.
    """
    get_node_type_schema(node_type)
    return cast(type[BasePipelineNode], resolve_node_class(node_type))


def option_keys_for_node_type(node_type: str) -> frozenset[str] | None:
    """The option keys a single node type reads, or ``None`` if no such type is served."""
    return _option_keys_by_type().get(node_type)


def parameter_option_mapping(node_type: str) -> dict[str, OptionsSource]:
    """One node type's reference params, each mapped to the option list it draws its values from.

    A param is not always named for its list, which is the whole reason to carry the pairing around.
    For ``LLMResponseWithPrompt``::

        {
            "llm_provider_id": OptionsSource.llm_provider_id,
            "llm_provider_model_id": OptionsSource.llm_provider_model_id,
            "source_material_id": OptionsSource.source_material,
            "collection_id": OptionsSource.collection,
            "collection_index_ids": OptionsSource.collection_index,
            "tools": OptionsSource.tools,
            "custom_actions": OptionsSource.custom_actions,
            "synthetic_voice_id": OptionsSource.synthetic_voice_id,
        }
    """
    return {
        param: option_key
        for param, option_key in _sources_by_type().get(node_type, {}).items()
        if option_key in PARAMETER_OPTION_SOURCES
    }


def served_option_keys() -> frozenset[str]:
    """Every option key some listed node type can reference."""
    return frozenset().union(*_option_keys_by_type().values())


def unknown_node_type(requested_type: str) -> NotFound:
    """A 404 carrying why the name failed and what the client could have asked for instead."""
    if requested_type in _structural_types():
        detail = (
            f"Node type '{requested_type}' is managed by the server and cannot be created or "
            f"configured. It may appear as a node's `type` in /inspect/ responses."
        )
    else:
        detail = f"Unknown node type: {requested_type}"
    return NotFound({"detail": detail, "valid_types": _valid_type_names()})


def etag(payload: list | dict) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return f'W/"{digest[:32]}"'


def _available_schemas() -> list[dict]:
    """The builder schemas behind the listed node types. ``ui:can_add`` is False for the deprecated
    types and the structural ones the server manages."""
    return [schema for schema in get_node_schemas() if schema.get("ui:can_add")]


def _output_topology(schema: dict) -> dict:
    """How edges leave this node type. ``EndNode`` is the only terminating type and it is unlisted,
    so there is no zero-output case."""
    node_class = resolve_node_class(schema["title"])
    if node_class is not None and issubclass(node_class, PipelineRouterNode):
        return PER_KEYWORD_OUTPUT
    return SINGLE_OUTPUT


def _schema(node_schema: dict) -> dict:
    """The node's JSON Schema as served, with withheld params taken out of ``properties`` and
    ``required``."""
    served = {key: value for key, value in node_schema.items() if ":" not in key and key != "properties"}
    served["properties"] = {
        name: _property(name, prop) for name, prop in node_schema["properties"].items() if not prop.get("api:exclude")
    }
    if required := served.get("required"):
        served["required"] = [name for name in required if name in served["properties"]]
    return served


def _property(name: str, prop: dict) -> dict:
    """One node param, as served: namespaced keys dropped, the two that carry meaning re-added under
    client names, cross-param links attached."""
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
    """The node's help link, absolutised. ``ui:documentation_link`` is site-relative -- the builder
    joins it in the browser, an API client has no base to join it to."""
    link = schema.get("ui:documentation_link")
    if not link:
        return None
    if link.startswith("http"):
        return link
    return f"{settings.DOCUMENTATION_BASE_URL}{link}"


@cache
def _sources_by_type() -> dict[str, dict[str, OptionsSource]]:
    """``node type -> {param: the option list it draws from}``, read off ``ui:optionsSource``.

    Withheld params and params with no source are left out. Unlike
    :func:`parameter_option_mapping` this keeps the params whose list names no team records -- the
    prompt-variable lists among them -- so it is the whole declared mapping, references or not.
    """
    return {
        schema["title"]: {
            name: prop["ui:optionsSource"]
            for name, prop in schema["properties"].items()
            if not prop.get("api:exclude") and prop.get("ui:optionsSource")
        }
        for schema in _available_schemas()
    }


@cache
def _option_keys_by_type() -> dict[str, frozenset[str]]:
    """The `/pipeline/options/` keys each node type's params can draw from. A known type that reads
    nothing yields an empty set, not a missing key."""
    keys_by_type = {}
    for schema in _available_schemas():
        keys = set(_sources_by_type()[schema["title"]].values())
        if "llm_provider_id" in schema["properties"]:
            keys.add("default_llm_provider")
        keys_by_type[schema["title"]] = frozenset(keys)
    return keys_by_type


@cache
def _structural_types() -> frozenset[str]:
    """Types the server creates and manages. Unlisted, but ``/inspect/`` still reports them as the
    ``type`` of real nodes."""
    return frozenset(
        schema["title"]
        for schema in get_node_schemas()
        if not schema.get("ui:can_add") and not schema.get("ui:deprecated")
    )


def _valid_type_names() -> list[str]:
    return [node["type"] for node in get_node_types()]
