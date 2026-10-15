"""Building a value that satisfies a JSON Schema.

A request that asks for structured output or forces a tool call is validated by the
caller against the schema it sent, so lorem ipsum prose fails it. OCS's router node
swallows that failure and falls back to its default route, which looks like routing
working. Answering with a conforming value instead lets those paths be exercised.

Choices are deterministic: the first enum member, the lowest allowed number, the
shortest allowed array, a fixed example for a string `format`. A router schema is a
`Literal` of the configured keywords, so the first-enum-member rule sends every message
down the first branch. A string `pattern` is not honoured.
"""

import math
import operator
from functools import reduce

from . import lorem

MAX_DEPTH = 6


def sample(schema: dict, index: int = 0) -> object:
    """A value satisfying `schema`. `index` varies the lorem filling between calls."""
    root = schema if isinstance(schema, dict) else {}
    return _sample(schema=root, root=root, index=index, depth=0)


def _sample(schema: dict, root: dict, index: int, depth: int) -> object:
    if depth > MAX_DEPTH:
        return None
    schema = _resolve(schema=schema, root=root)

    if "const" in schema:
        return schema["const"]
    if enum := schema.get("enum"):
        return enum[0]
    if (branch := _combinator_branch(schema=schema, root=root)) is not None:
        return _sample(schema=branch, root=root, index=index, depth=depth + 1)

    return _by_type(type_name=_type_of(schema), schema=schema, root=root, index=index, depth=depth)


def _combinator_branch(schema: dict, root: dict) -> dict | None:
    """The first `anyOf`/`oneOf` option, or the `allOf` options merged into one schema."""
    if options := schema.get("anyOf") or schema.get("oneOf"):
        return options[0]
    if all_of := schema.get("allOf"):
        return reduce(lambda merged, option: _merge(left=merged, right=_resolve(schema=option, root=root)), all_of, {})
    return None


_TYPE_CONSTANTS = {"boolean": False, "null": None}


def _by_type(type_name: str, schema: dict, root: dict, index: int, depth: int) -> object:
    if type_name in _TYPE_CONSTANTS:
        return _TYPE_CONSTANTS[type_name]
    if type_name == "object":
        return _object(schema=schema, root=root, index=index, depth=depth)
    if type_name == "array":
        return _array(schema=schema, root=root, index=index, depth=depth)
    if type_name == "integer":
        return _integer(schema)
    if type_name == "number":
        return _number(schema)
    return _string(schema=schema, index=index)


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
        target = _dereference(pointer=pointer, root=root)
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
        name: _sample(
            schema=subschema if isinstance(subschema, dict) else {},
            root=root,
            index=index + position,
            depth=depth + 1,
        )
        for position, (name, subschema) in enumerate(properties.items())
    }


def _array(schema: dict, root: dict, index: int, depth: int) -> list:
    items = schema.get("items")
    if not isinstance(items, dict):
        return []
    min_items = schema.get("minItems")
    count = 1 if min_items is None else int(min_items)
    if (max_items := schema.get("maxItems")) is not None:
        count = min(count, int(max_items))
    return [_sample(schema=items, root=root, index=index + position, depth=depth + 1) for position in range(count)]


# Fixed examples for the string formats pydantic emits.
_FORMAT_EXAMPLES = {
    "date-time": "2024-01-01T00:00:00Z",
    "date": "2024-01-01",
    "time": "00:00:00Z",
    "duration": "P1D",
    "email": "lorem@example.com",
    "uri": "https://example.com/lorem",
    "uuid": "00000000-0000-4000-8000-000000000000",
    "ipv4": "192.0.2.1",
    "ipv6": "2001:db8::1",
    "hostname": "example.com",
}


def _string(schema: dict, index: int) -> str:
    if example := _FORMAT_EXAMPLES.get(schema.get("format")):
        return example
    text = lorem.words(count=3, index=index)
    minimum = int(schema.get("minLength") or 0)
    while len(text) < minimum:
        text = f"{text} {lorem.words(count=3, index=index + len(text))}"
    maximum = schema.get("maxLength")
    if maximum is not None:
        text = text[: int(maximum)]
    return text


def _bounds(schema: dict) -> tuple[float | None, bool, float | None, bool]:
    """The lower and upper bound, each with whether it is exclusive. The tighter bound wins."""
    lower, lower_exclusive = _bound(
        inclusive=schema.get("minimum"), exclusive=schema.get("exclusiveMinimum"), tighter=operator.ge
    )
    upper, upper_exclusive = _bound(
        inclusive=schema.get("maximum"), exclusive=schema.get("exclusiveMaximum"), tighter=operator.le
    )
    return lower, lower_exclusive, upper, upper_exclusive


def _bound(inclusive, exclusive, tighter) -> tuple[float | None, bool]:
    """One bound and whether it is exclusive. `tighter(a, b)` says whether `a` is at least as tight as `b`."""
    inclusive, exclusive = _numeric(inclusive), _numeric(exclusive)
    if exclusive is None:
        return inclusive, False
    if inclusive is None or tighter(exclusive, inclusive):
        return exclusive, True
    return inclusive, False


def _numeric(value) -> float | None:
    """`value` if it is a number. The draft-4 boolean `exclusiveMinimum` reads as absent."""
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _integer(schema: dict) -> int:
    """The lowest allowed integer, or the one nearest 0 when there is no lower bound."""
    return int(_stepped(schema=schema, step=_integer_step(schema)))


def _integer_step(schema: dict) -> int:
    """`multipleOf` when it is a whole number of at least 1, otherwise 1."""
    step = _numeric(schema.get("multipleOf"))
    if step is None or step < 1 or step != int(step):
        return 1
    return int(step)


def _number(schema: dict) -> float:
    """The lowest allowed number, or the one nearest 0 when there is no lower bound."""
    step = _numeric(schema.get("multipleOf"))
    if step is not None and step > 0:
        return float(_stepped(schema=schema, step=step))
    lower, lower_exclusive, upper, upper_exclusive = _bounds(schema)
    if lower is None:
        if upper is None or _zero_allowed(upper=upper, upper_exclusive=upper_exclusive):
            return 0.0
        return float(upper - 1 if upper_exclusive else upper)
    if not lower_exclusive:
        return float(lower)
    return float((lower + upper) / 2 if upper is not None else lower + 1)


def _stepped(schema: dict, step: float) -> float:
    """The lowest multiple of `step` inside the bounds, or the one nearest 0 without a lower bound."""
    lower, lower_exclusive, upper, upper_exclusive = _bounds(schema)
    if lower is not None:
        value = math.ceil(lower / step) * step
        return value + step if lower_exclusive and value == lower else value
    if upper is None or _zero_allowed(upper=upper, upper_exclusive=upper_exclusive):
        return 0
    value = math.floor(upper / step) * step
    return value - step if upper_exclusive and value == upper else value


def _zero_allowed(upper: float, upper_exclusive: bool) -> bool:
    """Whether 0 is under the upper bound."""
    return upper > 0 if upper_exclusive else upper >= 0
