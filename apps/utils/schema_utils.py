"""JSON Schema / OpenAPI helpers shared across apps."""

from copy import deepcopy


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
