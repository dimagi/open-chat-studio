from copy import deepcopy
from typing import Any, Literal

from django.core.exceptions import ValidationError
from langchain_community.tools import APIOperation
from langchain_community.utilities.openapi import OpenAPISpec
from openapi_pydantic import DataType
from pydantic import BaseModel, Field


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
    for _path, methods in openapi_spec["paths"].items():
        for _method, details in methods.items():
            for key in list(details.keys()):
                if key not in operation_keys:
                    del details[key]

    return openapi_spec


def resolve_references(openapi_spec: dict) -> dict:
    """
    Resolves all $ref references in an OpenAPI specification document.

    Args:
        openapi_spec: The OpenAPI specification document.

    Returns:
        The OpenAPI specification document with all $ref references resolved.
    """

    def resolve_ref(data: dict, path: str) -> dict:
        if "$ref" in data:
            ref = data["$ref"]
            if not ref[0] == "#":
                raise ValueError(f"External references are not supported: {ref}")

            ref_path = ref[1:].split("/")[1:]
            current = openapi_spec
            for p in ref_path:
                current = current[p]
            # preserve metadata fields
            extra = deepcopy(data)
            extra.pop("$ref")
            return {**deepcopy(current), **extra}
        elif isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict | list):
                    data[k] = resolve_ref(v, f"{path}/{k}")
                else:
                    data[k] = v
        elif isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, dict | list):
                    data[i] = resolve_ref(item, f"{path}/{i}")
                else:
                    data[i] = item
        return data

    return resolve_ref(deepcopy(openapi_spec), "")


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


def get_operations_from_spec_dict(spec_dict) -> list[APIOperationDetails]:
    spec = OpenAPISpec.from_spec_dict(spec_dict)
    return get_operations_from_spec(spec, spec_dict)


def get_operations_from_spec(spec, spec_dict=None) -> list[APIOperationDetails]:
    # When spec_dict is None, parameter locations (path/query/etc.) cannot be resolved;
    # all non-body parameters will default to param_in="query".
    resolved_spec = resolve_references(spec_dict) if spec_dict else None
    operations = []
    for path in spec.paths:
        for method in spec.get_methods_for_path(path):
            op = APIOperation.from_openapi_spec(spec, path, method)
            operations.append(
                APIOperationDetails(
                    operation_id=op.operation_id,
                    description=op.description,
                    path=path,
                    method=method,
                    parameters=_extract_parameters(op, resolved_spec, path, method),
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


def _extract_parameters(
    operation: APIOperation, resolved_spec: dict | None = None, path: str = "", method: str = ""
) -> list[ParameterDetail]:
    """Extract parameter details from OpenAPI spec.

    Extracts both query/path parameters and request body parameters.
    Looks up parameter location (path, query, etc.) from the resolved spec.
    """
    # Build a map of parameter name -> param_in from the resolved spec
    param_in_map = {}
    body_prop_schemas: dict[str, dict] = {}
    if resolved_spec and path and method:
        resolved_op = resolved_spec.get("paths", {}).get(path, {}).get(method, {})
        for param_spec in resolved_op.get("parameters", []):
            param_name = param_spec.get("name")
            param_in = param_spec.get("in", "query")
            if param_name:
                param_in_map[param_name] = param_in
        # Pre-resolve body property schemas for type lookup (handles anyOf/oneOf)
        body_schema = (
            resolved_op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
        )
        body_prop_schemas = body_schema.get("properties", {})

    parameters = []
    for prop in operation.properties:
        param_in = param_in_map.get(prop.name, "query")
        schema_type = prop.type
        if not isinstance(schema_type, str):
            # prop.type can be a DataType enum or a dynamically created enum class
            schema_type = schema_type.value if isinstance(schema_type, DataType) else "string"
        parameters.append(
            ParameterDetail(
                name=prop.name,
                required=prop.required,
                schema_type=schema_type,
                description=prop.description,
                default=prop.default,
                param_in=param_in,
            )
        )

    # Extract request body parameters (these are always in the body)
    if operation.request_body:
        for param in operation.request_body.properties:
            params = param.model_dump()
            if isinstance(param.type, DataType):
                params["schema_type"] = param.type.value
                # UGLY HACK! DataType.Array is converted into a string like "Array<DataType.STRING>"
                # See langchain_community/tools/openapi/utils/api_models.py:315
            elif param.type is not None and param.type.startswith("Array<"):
                params["schema_type"] = DataType.ARRAY.value
            elif param.type is None:
                # Type could not be determined by langchain (e.g. anyOf/oneOf schemas
                # from Pydantic v2); resolve from the spec_dict property schema.
                params["schema_type"] = _resolve_schema_type(body_prop_schemas.get(param.name, {}))
            params["param_in"] = "body"
            parameters.append(ParameterDetail.model_validate(params))

    return parameters
