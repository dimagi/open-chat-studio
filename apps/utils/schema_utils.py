"""JSON Schema / OpenAPI helpers shared across apps."""

import hashlib
import re
from copy import deepcopy

from pydantic import BaseModel, create_model, model_serializer

# Anthropic requires tool names and JSON schema `properties` keys to match this pattern; reused
# wherever we build a schema or tool name from a user-supplied string (evaluator output fields,
# OpenAPI operation/parameter names) so it survives being sent to Anthropic.
VALID_PROPERTY_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")

_INVALID_PROPERTY_CHARS = re.compile(r"[^a-zA-Z0-9_.-]")
_REPEATED_UNDERSCORES = re.compile(r"_{2,}")
_MAX_PROPERTY_NAME_LENGTH = 64


def sanitize_property_name(name: str, taken: set[str] | None = None) -> str:
    """Rewrites `name` so it matches `VALID_PROPERTY_NAME_PATTERN`."""
    if VALID_PROPERTY_NAME_PATTERN.match(name):
        sanitized = name
    else:
        sanitized = _REPEATED_UNDERSCORES.sub("_", _INVALID_PROPERTY_CHARS.sub("_", name))
        sanitized = sanitized[:_MAX_PROPERTY_NAME_LENGTH] or "field"

    if taken is None or sanitized not in taken:
        return sanitized

    suffix = f"_{hashlib.sha1(name.encode()).hexdigest()[:8]}"
    while True:
        candidate = f"{sanitized[: _MAX_PROPERTY_NAME_LENGTH - len(suffix)]}{suffix}"
        if candidate not in taken:
            return candidate
        suffix = f"_{hashlib.sha1((name + suffix).encode()).hexdigest()[:8]}"


class OriginalNameSerializerMixin(BaseModel):
    """Base for a model built by `create_model_with_sanitized_names`: renames each field back to
    its original (pre-sanitization) name whenever the model is serialized. This makes the rename
    transparent to every caller of `model_dump()` -- directly, or via anything built on top of it,
    e.g. a nested model's own dump, or LangChain's structured-output result -- rather than
    requiring everyone to remember to call something other than the normal `model_dump()`.
    """

    @model_serializer(mode="wrap")
    def _serialize_with_original_names(self, handler) -> dict:
        dumped = handler(self)
        mapping = getattr(type(self), "__ocs_field_name_mapping__", {})
        return {mapping.get(key, key): value for key, value in dumped.items()}


def create_model_with_sanitized_names(model_name: str, fields: dict[str, tuple], **kwargs) -> type[BaseModel]:
    """Builds a Pydantic model from `fields` -- the same `{name: (type, FieldInfo)}` shape
    `pydantic.create_model` takes -- sanitizing each key so the model's JSON schema `properties`
    are valid tool/property names for every provider (notably Anthropic's
    `^[a-zA-Z0-9_.-]{1,64}$`). The sanitized-to-original mapping is stashed on the model as
    `__ocs_field_name_mapping__` and applied by `OriginalNameSerializerMixin`, so a plain
    `model_dump()` on an instance already comes back keyed by the names in `fields` rather than
    the sanitized ones the schema (and so the LLM) sees.

    Args:
        model_name: Name for the generated Pydantic model.
        fields: Mapping of field name to a `(type, FieldInfo)` tuple, as `pydantic.create_model`
            expects.
        **kwargs: Passed through to `pydantic.create_model` (e.g. `__doc__`).

    Returns:
        Dynamically created Pydantic BaseModel class.
    """
    sanitized_fields = {}
    field_name_mapping: dict[str, str] = {}
    taken: set[str] = set()

    for field_name, field_spec in fields.items():
        sanitized_name = sanitize_property_name(field_name, taken)
        taken.add(sanitized_name)
        field_name_mapping[sanitized_name] = field_name
        sanitized_fields[sanitized_name] = field_spec

    model = create_model(model_name, __base__=OriginalNameSerializerMixin, **sanitized_fields, **kwargs)
    model.__ocs_field_name_mapping__ = field_name_mapping
    return model


def resolve_references(openapi_spec: dict) -> dict:
    """Returns a copy of `openapi_spec` with every internal `$ref` replaced by what it points at.

    Substitution is one level deep: a target is inserted as `openapi_spec` wrote it, so a `$ref`
    nested inside one survives. That is what makes a self-referencing schema terminate here, and it
    is why a caller that drops `$defs` afterwards should check nothing still points into it.
    """
    return _resolve(deepcopy(openapi_spec), openapi_spec)


def _resolve(data, spec: dict):
    """Walks `data`, swapping each `$ref` node for its target in `spec`."""
    if isinstance(data, dict):
        if "$ref" in data:
            return _resolve_ref(data, spec)
        return {key: _resolve(value, spec) for key, value in data.items()}
    if isinstance(data, list):
        return [_resolve(item, spec) for item in data]
    return data


def _resolve_ref(node: dict, spec: dict) -> dict:
    """The target of a `$ref`, keeping any metadata fields sitting alongside the `$ref` itself."""
    ref = node["$ref"]
    if not ref.startswith("#"):
        raise ValueError(f"External references are not supported: {ref}")

    target = spec
    for key in ref[1:].split("/")[1:]:
        target = target[key]

    extra = {key: value for key, value in node.items() if key != "$ref"}
    return {**deepcopy(target), **extra}


def collapse_optional_types(schema: dict) -> None:
    """Rewrites each `X | None` property of `schema` as a plain `X`, in place.

    Pydantic renders an optional field as `anyOf: [{"type": "x"}, {"type": "null"}]`, which says
    "x or null" where `required` already says whether the field may be omitted. A union that holds
    no single named type -- `Any | None`, `Literal["a", 1] | None`, `list[str] | int | None` -- is
    left exactly as pydantic wrote it: there is nothing to collapse to, and naming one member's
    type would rule out values the field accepts.
    """
    for prop in schema.get("properties", {}).values():
        if sole_type := _sole_type(prop.get("anyOf", ())):
            prop.pop("anyOf")
            prop["type"] = sole_type


def _sole_type(any_of) -> str | None:
    """The one type a union permits besides `null`, or None where it permits more than one or names
    none. A member can carry no `type` at all -- `Any` renders as `{}`, a mixed-value `Literal` as a
    bare `enum` -- so this reads them defensively."""
    types = [member.get("type") for member in any_of if member.get("type") != "null"]
    return types[0] if len(types) == 1 and types[0] else None
