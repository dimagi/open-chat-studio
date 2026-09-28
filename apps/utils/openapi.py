"""Typed access to an OpenAPI document."""

import copy
import re
from typing import Self

from openapi_pydantic import OpenAPI, Operation, Parameter, PathItem, Reference, RequestBody, Schema
from pydantic import ValidationError

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
                if not any(_remove_invalid_part(spec_dict, error["loc"]) for error in errors):
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


def _remove_invalid_part(document: dict, loc: tuple) -> bool:
    """Delete the deepest entry of `document` on the error path `loc`. Returns False if none exists."""
    parent, key, node = None, None, document
    for part in loc:
        is_dict_part = isinstance(node, dict) and part in node
        is_list_part = isinstance(node, list) and isinstance(part, int) and part < len(node)
        if is_dict_part or is_list_part:
            parent, key, node = node, part, node[part]
        else:
            break
    if parent is None:
        return False
    del parent[key]
    return True
