from typing import Any, Literal

from django.core.exceptions import ValidationError
from pydantic import BaseModel, Field

from apps.utils.openapi import OpenAPISpec
from apps.utils.schema_utils import resolve_references


def get_standalone_schema_for_action_operation(action_operation):
    action = action_operation.custom_action
    ops_by_id = action.get_operations_by_id()
    operation = ops_by_id.get(action_operation.operation_id)
    if not operation:
        raise ValidationError("Custom action operation is no longer available")

    return get_standalone_spec(action.server_url, action.api_schema, operation.path, operation.method)


def get_standalone_spec(server_url: str, openapi_spec: dict, path: str, method: str):
    """Returns a standalone OpenAPI spec for a single operation."""
    openapi_spec = trim_spec(openapi_spec)
    info = openapi_spec["info"]
    info["title"] += f" - {method} {path}"
    info["description"] = f"Standalone OpenAPI spec for {method} {path}"
    paths = openapi_spec.pop("paths")
    openapi_spec["paths"] = {path: {method: paths[path][method]}}
    openapi_spec["servers"] = [{"url": server_url}]
    return openapi_spec


def trim_spec(openapi_spec: dict) -> dict:
    """Removes unnecessary keys from the OpenAPI spec.
    If there are any refs in the schema, they will be resolved.
    """
    openapi_spec = resolve_references(openapi_spec)
    top_level_keys = ["openapi", "info", "paths"]
    for key in list(openapi_spec.keys()):
        if key not in top_level_keys:
            del openapi_spec[key]

    operation_keys = ["parameters", "requestBody", "tags", "summary", "description", "operationId"]
    for methods in openapi_spec["paths"].values():
        for details in methods.values():
            for key in list(details.keys()):
                if key not in operation_keys:
                    del details[key]

    return openapi_spec


class ParameterDetail(BaseModel):
    """Represents a single parameter in an API operation."""

    name: str
    description: str | None = None
    required: bool = False
    schema_type: str = Field(default="string")
    default: Any = None
    # "body" is a synthetic value used internally for request body parameters;
    # it does not correspond to an OpenAPI 3.x "in" value.
    param_in: Literal["path", "query", "body", "header", "cookie"] = "query"

    def get_default_value(self) -> Any:
        """Return the default value for this parameter, with type-appropriate fallbacks."""
        if self.default is not None:
            return self.default
        return {
            "boolean": False,
            "integer": 0,
            "number": 0.0,
            "array": [],
            "object": {},
        }.get(self.schema_type, "")


class APIOperationDetails(BaseModel):
    operation_id: str
    description: str | None = None
    path: str
    method: str
    parameters: list[ParameterDetail] = []

    @property
    def path_parameters(self) -> list["ParameterDetail"]:
        return [p for p in self.parameters if p.param_in == "path"]

    @property
    def query_parameters(self) -> list["ParameterDetail"]:
        return [p for p in self.parameters if p.param_in == "query"]

    @property
    def body_parameters(self) -> list["ParameterDetail"]:
        return [p for p in self.parameters if p.param_in == "body"]

    def __str__(self):
        return f"{self.method.upper()}: {self.description or self.operation_id}"


PARAMETER_LOCATIONS = ("path", "query", "header", "cookie")


def get_operations_from_spec_dict(spec_dict: dict) -> list[APIOperationDetails]:
    return get_operations_from_spec(OpenAPISpec.from_spec_dict(spec_dict), spec_dict)


def get_operations_from_spec(spec: OpenAPISpec, spec_dict: dict) -> list[APIOperationDetails]:
    resolved_spec = resolve_references(spec_dict)
    operations = []
    for path in spec.paths or {}:
        for method in spec.get_methods_for_path(path):
            operation = spec.get_operation(path, method)
            resolved_operation = resolved_spec.get("paths", {}).get(path, {}).get(method, {})
            operations.append(
                APIOperationDetails(
                    operation_id=spec.get_cleaned_operation_id(operation, path, method),
                    description=spec.get_operation_description(path, operation),
                    path=path,
                    method=method,
                    parameters=_extract_parameters(resolved_operation, spec_dict),
                )
            )
    return operations


def _resolve_schema_type(prop_schema: dict) -> str:
    """Resolve the type from a property schema, handling anyOf/oneOf patterns.

    Pydantic v2 generates schemas like ``{"anyOf": [{"type": "boolean"}]}``
    instead of ``{"type": "boolean"}``.  This helper unwraps that pattern,
    filtering out ``"null"`` variants (used for Optional fields), and falls
    back to ``"string"`` when the type cannot be determined.
    """
    if "type" in prop_schema:
        return prop_schema["type"]
    for key in ("anyOf", "oneOf"):
        variants = prop_schema.get(key, [])
        non_null = [item for item in variants if item.get("type") != "null"]
        if len(non_null) == 1 and "type" in non_null[0]:
            return non_null[0]["type"]
    return "string"


def _follow_ref(node: dict, spec_dict: dict) -> dict:
    """The target at the end of `node`'s `$ref` chain, or `node` itself when the chain is cyclic or dangling."""
    seen = set()
    current = node
    while isinstance(current, dict) and "$ref" in current:
        ref = current["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return node
        seen.add(ref)
        target = spec_dict
        for key in ref[2:].split("/"):
            if not isinstance(target, dict) or key not in target:
                return node
            target = target[key]
        if not isinstance(target, dict):
            return node
        current = {**target, **{key: value for key, value in current.items() if key != "$ref"}}
    return current


def _extract_parameters(resolved_operation: dict, spec_dict: dict) -> list[ParameterDetail]:
    """Read the parameters and JSON request body properties of an operation with its references resolved."""
    parameters = []
    for param in resolved_operation.get("parameters", []):
        param = _follow_ref(param, spec_dict)
        if not param.get("name") or param.get("in") not in PARAMETER_LOCATIONS:
            continue
        schema = _follow_ref(param.get("schema", {}), spec_dict)
        parameters.append(
            ParameterDetail(
                name=param["name"],
                description=param.get("description"),
                required=param.get("required", False),
                schema_type=_resolve_schema_type(schema),
                default=schema.get("default"),
                param_in=param["in"],
            )
        )

    request_body = _follow_ref(resolved_operation.get("requestBody", {}), spec_dict)
    body_schema = request_body.get("content", {}).get("application/json", {}).get("schema")
    if body_schema is None:
        return parameters
    body_schema = _follow_ref(body_schema, spec_dict)

    properties = body_schema.get("properties")
    if body_schema.get("type") == "object" and properties:
        required = set(body_schema.get("required", []))
        for name, prop in properties.items():
            prop = _follow_ref(prop, spec_dict)
            parameters.append(
                ParameterDetail(
                    name=name,
                    description=prop.get("description"),
                    required=name in required,
                    schema_type=_resolve_schema_type(prop),
                    default=prop.get("default"),
                    param_in="body",
                )
            )
    else:
        parameters.append(
            ParameterDetail(
                name="body",
                description=body_schema.get("description"),
                required=True,
                schema_type=_resolve_schema_type(body_schema),
                default=body_schema.get("default"),
                param_in="body",
            )
        )
    return parameters
