"""Typed access to an OpenAPI document."""

import copy
import re
from typing import Literal, Self, overload

from openapi_pydantic import OpenAPI, Operation, Parameter, PathItem, Reference, RequestBody, Schema
from pydantic import ValidationError
from pydantic_core import ErrorDetails

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


class OpenAPISpec(OpenAPI):
    """An OpenAPI document that drops the parts of the spec that fail validation."""

    # openapi-pydantic only accepts 3.1.x; 3.0 and Swagger documents are parsed as best effort.
    openapi: str = "3.1.0"

    @classmethod
    def from_spec_dict(cls, spec_dict: dict) -> Self:
        if not isinstance(spec_dict.get("openapi"), str) and not isinstance(spec_dict.get("swagger"), str):
            raise ValueError("The document is not an OpenAPI or Swagger spec")
        spec_dict = copy.deepcopy(spec_dict)
        while True:
            try:
                return cls.model_validate(spec_dict)
            except ValidationError as e:
                errors = e.errors()
                if not _remove_invalid_parts(spec_dict, errors):
                    raise ValueError(f"Invalid OpenAPI spec: {errors[0]['msg']}") from e

    @property
    def base_url(self) -> str:
        return self.servers[0].url

    def get_methods_for_path(self, path: str) -> list[str]:
        path_item = self._get_path_item(path)
        return [method for method in HTTP_METHODS if isinstance(getattr(path_item, method, None), Operation)]

    def get_operation(self, path: str, method: str) -> Operation:
        operation = getattr(self._get_path_item(path), method, None)
        if not isinstance(operation, Operation):
            raise ValueError(f"No {method} method found for {path}")
        return operation

    def get_parameters_for_path(self, path: str) -> list[Parameter]:
        return [self._resolve_parameter(p) for p in self._get_path_item(path).parameters or []]

    def get_parameters_for_operation(self, operation: Operation) -> list[Parameter]:
        return [self._resolve_parameter(p) for p in operation.parameters or []]

    def get_request_body_for_operation(self, operation: Operation) -> RequestBody | None:
        request_body, seen = operation.requestBody, set()
        while isinstance(request_body, Reference):
            request_body = self._get_component("requestBodies", _visit(request_body, seen))
        return request_body

    def get_referenced_schema(self, ref: Reference) -> Schema:
        return self._get_component("schemas", ref)

    def get_schema(self, schema: Reference | Schema) -> Schema:
        """Resolve a schema and, in place, the references in its properties, items and subschemas."""
        return self._resolve_schema(schema, frozenset())

    def get_operation_description(self, path: str, operation: Operation) -> str:
        path_item = self._get_path_item(path)
        return operation.description or operation.summary or path_item.description or path_item.summary or ""

    @staticmethod
    def get_cleaned_operation_id(operation: Operation, path: str, method: str) -> str:
        operation_id = operation.operationId
        if operation_id is None:
            operation_id = f"{re.sub(r'[^a-zA-Z0-9]', '_', path.lstrip('/'))}_{method}"
        return operation_id.replace("-", "_").replace(".", "_").replace("/", "_")

    def _get_path_item(self, path: str) -> PathItem:
        path_item = (self.paths or {}).get(path)
        if not path_item:
            raise ValueError(f"No path found for {path}")
        return path_item

    def _resolve_parameter(self, parameter: Parameter | Reference) -> Parameter:
        seen = set()
        while isinstance(parameter, Reference):
            parameter = self._get_component("parameters", _visit(parameter, seen))
        return parameter

    def _resolve_schema(self, schema: Reference | Schema, seen: frozenset[str]) -> Schema:
        """`seen` holds the references on the path from the root schema, so a schema used twice is not a cycle."""
        if isinstance(schema, Reference):
            if schema.ref in seen:
                raise ValueError(f"Cyclic reference: {schema.ref}")
            seen = seen | {schema.ref}
            schema = self.get_referenced_schema(schema)
        if schema.properties is not None:
            for name, prop in schema.properties.items():
                schema.properties[name] = self._resolve_schema(prop, seen)
        if schema.items is not None:
            schema.items = self._resolve_schema(schema.items, seen)
        for key in ("allOf", "anyOf", "oneOf"):
            if (subschemas := getattr(schema, key)) is not None:
                setattr(schema, key, [self._resolve_schema(subschema, seen) for subschema in subschemas])
        return schema

    @overload
    def _get_component(self, kind: Literal["schemas"], ref: Reference) -> Schema: ...

    @overload
    def _get_component(self, kind: Literal["parameters"], ref: Reference) -> Parameter | Reference: ...

    @overload
    def _get_component(self, kind: Literal["requestBodies"], ref: Reference) -> RequestBody | Reference: ...

    def _get_component(self, kind: str, ref: Reference) -> Parameter | RequestBody | Schema | Reference:
        name = ref.ref.split("/")[-1]
        components = getattr(self.components, kind, None) or {}
        if name not in components:
            raise ValueError(f"No {kind} component found for {name}")
        return components[name]


def _visit(ref: Reference, seen: set[str]) -> Reference:
    """Add `ref` to `seen`, raising if it is already there."""
    if ref.ref in seen:
        raise ValueError(f"Cyclic reference: {ref.ref}")
    seen.add(ref.ref)
    return ref


def _remove_invalid_parts(document: dict, errors: list[ErrorDetails]) -> bool:
    """Delete the entry of `document` that each error points at. Returns False if there was none."""
    targets = {}
    for error in errors:
        if path := _invalid_part(document, error):
            targets.setdefault(path, None)

    # Reverse order deletes children before parents and later list items before earlier ones.
    for path in sorted(targets, key=lambda path: [(isinstance(part, int), part) for part in path], reverse=True):
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        del parent[path[-1]]
    return bool(targets)


def _invalid_part(document: dict, error: ErrorDetails) -> tuple:
    """The path of the entry of `document` to delete for `error`, or an empty tuple when there is none."""
    if error["type"] == "missing" and error["loc"][-1] == "$ref":
        # `$ref` is the only required field of `Reference`: this is the failed branch of a `X | Reference` union.
        return ()
    path = _existing_path(document, error["loc"])
    if path and path[-1] in ("name", "in") and _is_parameter(path[:-1]):
        path = path[:-1]
    if path and _is_essential(document, path):
        raise ValueError(f"Invalid OpenAPI spec: {error['msg']} at {'/'.join(map(str, error['loc']))}")
    return path


def _existing_path(document: dict, loc: tuple) -> tuple:
    """The parts of the error location `loc` that exist in `document`, skipping union member tags."""
    path, node = [], document
    for part in loc:
        is_dict_part = isinstance(node, dict) and part in node
        is_list_part = isinstance(node, list) and isinstance(part, int) and part < len(node)
        if is_dict_part or is_list_part:
            path.append(part)
            node = node[part]
    return tuple(path)


def _is_parameter(path: tuple) -> bool:
    """Whether `path` is an entry of an operation's, a path's or the components' parameters."""
    return len(path) >= 2 and path[-2] == "parameters" and (isinstance(path[-1], int) or path[-3:-2] == ("components",))


def _is_essential(document: dict, path: tuple) -> bool:
    """Whether `path` is a required parameter or a request body, its content, or its schema."""
    if _is_parameter(path):
        node = document
        for part in path:
            node = node[part]
        return isinstance(node, dict) and node.get("required") is True
    return any(
        _is_request_body(path[: index + 1]) and _is_body_content_or_schema(path[index + 1 :])
        for index in range(len(path))
    )


def _is_request_body(path: tuple) -> bool:
    """Whether `path` is an operation's request body or a request body in the components."""
    is_operation_body = len(path) >= 2 and path[-1] == "requestBody" and path[-2] in HTTP_METHODS
    return is_operation_body or path[-3:-1] == ("components", "requestBodies")


def _is_body_content_or_schema(rest: tuple) -> bool:
    """Whether `rest`, relative to a request body, is the body, its content, a media type or a media type's schema."""
    return not rest or (rest[0] == "content" and (len(rest) <= 2 or rest[2:] == ("schema",)))
