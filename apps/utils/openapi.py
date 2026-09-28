"""Typed access to an OpenAPI document."""

import copy
import re
from typing import Self

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
        request_body = operation.requestBody
        while isinstance(request_body, Reference):
            request_body = self._get_component("requestBodies", request_body)
        return request_body

    def get_referenced_schema(self, ref: Reference) -> Schema:
        return self._get_component("schemas", ref)

    def get_schema(self, schema: Reference | Schema) -> Schema:
        """Resolve a schema and, in place, the references in its properties and items."""
        if isinstance(schema, Reference):
            schema = self.get_referenced_schema(schema)
        if schema.properties is not None:
            for name, prop in schema.properties.items():
                schema.properties[name] = self.get_schema(prop)
        if schema.items is not None:
            schema.items = self.get_schema(schema.items)
        return schema

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
        while isinstance(parameter, Reference):
            parameter = self._get_component("parameters", parameter)
        return parameter

    def _get_component(self, kind: str, ref: Reference):
        name = ref.ref.split("/")[-1]
        components = getattr(self.components, kind, None) or {}
        if name not in components:
            raise ValueError(f"No {kind} component found for {name}")
        return components[name]


def _remove_invalid_parts(document: dict, errors: list[ErrorDetails]) -> bool:
    """Delete the entry of `document` that each error points at. Returns False if there was none."""
    targets = {}
    for error in errors:
        if error["type"] == "missing" and error["loc"][-1] == "$ref":
            # `$ref` is the only required field of `Reference`: this is the failed branch of a `X | Reference` union.
            continue
        path = _existing_path(document, error["loc"])
        if not path:
            continue
        if path[-1] in ("name", "in") and _is_parameter(path[:-1]):
            path = path[:-1]
        if _is_essential(document, path):
            raise ValueError(f"Invalid OpenAPI spec: {error['msg']} at {'/'.join(map(str, error['loc']))}")
        targets.setdefault(path, None)

    # Reverse order deletes children before parents and later list items before earlier ones.
    for path in sorted(targets, key=lambda path: [(isinstance(part, int), part) for part in path], reverse=True):
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        del parent[path[-1]]
    return bool(targets)


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
    node = document
    for part in path:
        node = node[part]
    if _is_parameter(path):
        return isinstance(node, dict) and node.get("required") is True

    for index, part in enumerate(path):
        is_operation_body = part == "requestBody" and index > 0 and path[index - 1] in HTTP_METHODS
        is_component_body = path[index - 2 : index] == ("components", "requestBodies")
        if not (is_operation_body or is_component_body):
            continue
        rest = path[index + 1 :]
        if not rest or (rest[0] == "content" and (len(rest) <= 2 or rest[2:] == ("schema",))):
            return True
    return False
