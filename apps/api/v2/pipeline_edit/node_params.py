"""Turning a request body's params into the params a node row holds (#4140).

A write stores the node model's own dump, not the body's dict. Nothing here reads the JSON Schema
``/pipeline/nodes/`` publishes — that is derived from these same models, so checking against it
would be checking against a copy.
"""

import types
import typing
from typing import Any

from django.db import DatabaseError

from apps.pipelines.nodes.base import BasePipelineNode, UiSchema


def writable_params(node_class: type[BasePipelineNode], params: dict[str, Any]) -> dict[str, Any]:
    """``params`` narrowed to the ones a client may set on this type.

    Drops a name the type does not declare -- it would be stored and then ignored at run time -- and
    one the discovery API withholds via ``UiSchema.api_exclude``: never offered, so not settable.
    """
    return {name: value for name, value in params.items() if _is_writable(node_class, name)}


def node_params(node_class: type[BasePipelineNode], node_id: str, merged: dict[str, Any]) -> dict[str, Any]:
    """``merged`` as the type defines it: normalised where it parses, filtered where it does not.

    Validation reports rather than refuses — a node that does not parse is stored and its errors come
    back in ``pipeline_errors``, which is how a node gets built up over several calls and how the UI
    builder saves one. ``Pipeline.validate`` tolerates every way a node can fail to parse, so this
    cannot wedge a later read.
    """
    declared = {name: value for name, value in merged.items() if name in node_class.model_fields}
    try:
        model = node_class.model_validate({**declared, "node_id": node_id, "django_node": None})
    except DatabaseError:
        # Caught inside the row lock, a DB error leaves the transaction aborted and the next query
        # raises with nothing to say about where it came from. `Pipeline.validate` re-raises too.
        raise
    except Exception:  # noqa: BLE001 - every other way params fail to parse is reported, not refused
        return declared
    return model.model_dump(mode="json")


def is_list_param(node_class: type[BasePipelineNode], name: str) -> bool:
    """Whether the type declares this param list-valued.

    By the declaration, not the value: reading a one-element array as a list is what let a wrapped
    scalar id pass the resource check.
    """
    field = node_class.model_fields.get(name)
    if field is None:
        return False
    return _is_list_annotation(field.annotation)


def _is_writable(node_class: type[BasePipelineNode], name: str) -> bool:
    field = node_class.model_fields.get(name)
    if field is None:
        return False
    if field.exclude:
        # `node_id` and `django_node`: the model's own internals rather than params. Never offered,
        # and a client-supplied one collides with the keyword argument
        # `Node.pipeline_node_instance` passes.
        return False
    extra = field.json_schema_extra
    return not (isinstance(extra, UiSchema) and extra.api_exclude)


def _is_list_annotation(annotation: Any) -> bool:
    """Whether an annotation is a list or an optional one — ``list[int]``, ``list[int] | None``."""
    origin = typing.get_origin(annotation)
    if origin in (list, set, tuple):
        return True
    if origin is typing.Union or origin is types.UnionType:
        return any(_is_list_annotation(arg) for arg in typing.get_args(annotation))
    return False
