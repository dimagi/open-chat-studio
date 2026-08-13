"""JSON Schema / OpenAPI helpers shared across apps."""

from copy import deepcopy


def resolve_references(openapi_spec: dict) -> dict:
    """Returns a copy of `openapi_spec` with every internal `$ref` replaced by what it points at."""
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
    "x or null" where `required` already says whether the field may be omitted.
    """
    for prop in schema.get("properties", {}).values():
        if "anyOf" in prop:
            any_of = prop.pop("anyOf")
            prop["type"] = [member["type"] for member in any_of if member["type"] != "null"][0]
