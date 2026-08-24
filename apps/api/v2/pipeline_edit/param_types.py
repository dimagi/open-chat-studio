"""Type checking for node params, against the JSON Schema `/pipeline/nodes/` published (#4140).

The façade is deliberately lenient about a graph that is *incomplete* — a missing required param
lands and is reported, so an agent can build a node up over several calls. A param whose value is
the wrong shape is a different thing, and is refused before anything is written.

The reason is that it is not recoverable. ``Pipeline.validate()`` parses every node on every read,
and only ``pydantic.ValidationError`` and ``PipelineNodeBuildError`` are reported rather than
raised; a value that fails earlier than pydantic's own coercion (a non-string reaching
``CodeNode``'s reserved-key scan, a list reaching an FK column) escapes as an untyped exception. It
would then break ``/inspect/`` and every later façade write on that pipeline, including the
response to the write that stored it -- so the caller would never learn the ``node_id`` it needs to
delete the node again.
"""

from typing import Any

#: JSON Schema type -> the Python types a decoded JSON value of that type can have. ``bool`` is
#: handled separately below: it is an ``int`` subclass in Python, but a client that sent ``true``
#: for an integer param did not mean ``1``.
JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}

#: Types for which a ``bool`` is an acceptable value. Anywhere else it is rejected on sight.
BOOL_IS_VALID_FOR = frozenset({"boolean"})


def param_type_errors(properties: dict, params: dict[str, Any]) -> dict[str, str]:
    """``param -> why its value does not fit the declared type``, for the params actually sent.

    ``None`` is skipped whatever the declared type: it is how a param is cleared, and the node
    models take it for every optional field.
    """
    errors: dict[str, str] = {}
    for name, value in params.items():
        if value is None:
            continue
        if message := _type_error(properties.get(name, {}), value):
            errors[name] = message
    return errors


def is_array_param(properties: dict, name: str) -> bool:
    """Whether the type declares this param as list-valued.

    Used to decide whether a value is a list *of* references or a single reference, rather than
    inferring it from the value: inferring is what let a scalar id wrapped in a one-element array
    pass the reference check as though the param took a list.
    """
    return properties.get(name, {}).get("type") == "array"


def _type_error(prop: dict, value: Any) -> str | None:
    """Why ``value`` does not fit ``prop``, or ``None`` if it does.

    Served properties only ever state a plain ``type``, optionally with ``items`` or ``enum``, so
    there is no ``anyOf``/``$ref`` to resolve here — ``test_every_served_param_states_a_plain_type``
    holds that true. A param the schema says nothing about is left alone; an undeclared name is
    ``check_param_names``'s to refuse, not this.
    """
    expected = prop.get("type")
    if expected is None:
        return None
    if not _matches(expected, value):
        return f"Expected {expected}, got {json_type_name(value)}."
    if expected == "array":
        return _item_type_error(prop, value)
    return None


def _item_type_error(prop: dict, values: list) -> str | None:
    """Why an array's entries do not fit its ``items`` type. Entries are checked one by one because
    a list-valued param is a list of ids or of names, and one bad entry spoils the write."""
    item_type = prop.get("items", {}).get("type")
    if not item_type:
        return None
    wrong = [item for item in values if item is not None and not _matches(item_type, item)]
    if not wrong:
        return None
    return f"Expected an array of {item_type}, got {json_type_name(wrong[0])}: {wrong[0]!r}."


def _matches(expected: str, value: Any) -> bool:
    allowed = JSON_TYPES.get(expected)
    if allowed is None:
        # A type the map does not cover ("null", or something added to JSON Schema later) is not
        # something to refuse a write over.
        return True
    if isinstance(value, bool) and expected not in BOOL_IS_VALID_FOR:
        return False
    return isinstance(value, allowed)


def json_type_name(value) -> str:
    """What a decoded value would be called in JSON, for an error message."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
