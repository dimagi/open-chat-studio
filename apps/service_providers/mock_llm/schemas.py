"""Building a value that satisfies a JSON Schema.

A request that asks for structured output or forces a tool call is validated by the
caller against the schema it sent, so lorem ipsum prose fails it. OCS's router node
swallows that failure and falls back to its default route, which looks like routing
working. Answering with a conforming value instead lets those paths be exercised.

Choices are deterministic: the first enum member, the lowest allowed number, the
shortest allowed array. A router schema is a `Literal` of the configured keywords, so
the first-enum-member rule sends every message down the first branch.
"""

from . import lorem

MAX_DEPTH = 6


def sample(schema: dict, index: int = 0) -> object:
    """A value satisfying `schema`. `index` varies the lorem filling between calls."""
    root = schema if isinstance(schema, dict) else {}
    return _sample(root, root, index, 0)


def _sample(schema: dict, root: dict, index: int, depth: int) -> object:
    if depth > MAX_DEPTH:
        return None
    schema = _resolve(schema, root)

    if "const" in schema:
        return schema["const"]
    if enum := schema.get("enum"):
        return enum[0]
    for combinator in ("anyOf", "oneOf"):
        if options := schema.get(combinator):
            return _sample(options[0], root, index, depth + 1)
    if all_of := schema.get("allOf"):
        merged: dict = {}
        for option in all_of:
            merged = _merge(merged, _resolve(option, root))
        return _sample(merged, root, index, depth + 1)

    return _by_type(_type_of(schema), schema, root, index, depth)


def _by_type(type_name: str, schema: dict, root: dict, index: int, depth: int) -> object:
    if type_name == "object":
        return _object(schema, root, index, depth)
    if type_name == "array":
        return _array(schema, root, index, depth)
    if type_name == "string":
        return _string(schema, index)
    if type_name == "integer":
        return int(_number(schema))
    if type_name == "number":
        return _number(schema)
    if type_name == "boolean":
        return False
    if type_name == "null":
        return None
    return _string(schema, index)


def _type_of(schema: dict) -> str:
    """The schema's type, picking the first non-null entry when it lists several."""
    declared = schema.get("type")
    if isinstance(declared, list):
        return next((entry for entry in declared if entry != "null"), "null")
    if isinstance(declared, str):
        return declared
    return "object" if "properties" in schema else "string"


def _resolve(schema: dict, root: dict) -> dict:
    """Follow a local `$ref`, which pydantic emits for a nested model."""
    seen = set()
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        pointer = schema["$ref"]
        if pointer in seen:
            return {}
        seen.add(pointer)
        target = _dereference(pointer, root)
        if target is None:
            return {}
        schema = {**target, **{key: value for key, value in schema.items() if key != "$ref"}}
    return schema if isinstance(schema, dict) else {}


def _dereference(pointer: str, root: dict) -> dict | None:
    if not pointer.startswith("#/"):
        return None
    node: object = root
    for part in pointer.removeprefix("#/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def _merge(left: dict, right: dict) -> dict:
    merged = {**left, **right}
    if "properties" in left and "properties" in right:
        merged["properties"] = {**left["properties"], **right["properties"]}
    if "required" in left and "required" in right:
        merged["required"] = [*left["required"], *right["required"]]
    return merged


def _object(schema: dict, root: dict, index: int, depth: int) -> dict:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    # Every declared property is filled, not just the required ones: OpenAI's strict mode
    # requires them all, and a fuller object is more use to whatever consumes it.
    return {
        name: _sample(subschema if isinstance(subschema, dict) else {}, root, index + position, depth + 1)
        for position, (name, subschema) in enumerate(properties.items())
    }


def _array(schema: dict, root: dict, index: int, depth: int) -> list:
    items = schema.get("items")
    if not isinstance(items, dict):
        return []
    count = max(int(schema.get("minItems") or 1), 1)
    if max_items := schema.get("maxItems"):
        count = min(count, max(int(max_items), 0))
    return [_sample(items, root, index + position, depth + 1) for position in range(count)]


def _string(schema: dict, index: int) -> str:
    text = lorem.words(3, index=index)
    minimum = int(schema.get("minLength") or 0)
    while len(text) < minimum:
        text = f"{text} {lorem.words(3, index=index + len(text))}"
    maximum = schema.get("maxLength")
    if maximum is not None:
        text = text[: int(maximum)]
    return text


def _number(schema: dict) -> float:
    for key in ("minimum", "exclusiveMinimum"):
        if (value := schema.get(key)) is not None:
            return float(value) + (1 if key == "exclusiveMinimum" else 0)
    for key in ("maximum", "exclusiveMaximum"):
        if (value := schema.get(key)) is not None:
            return float(value) - (1 if key == "exclusiveMaximum" else 0)
    return 0.0
