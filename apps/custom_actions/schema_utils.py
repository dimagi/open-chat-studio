from copy import deepcopy
from typing import Any

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
    param_in: str = Field(default="query")  # where the parameter is used: "path", "query", "header", "cookie"


class APIOperationDetails(BaseModel):
    operation_id: str
    description: str
    path: str
    method: str
    parameters: list[ParameterDetail] = []

    def __str__(self):
        return f"{self.method.upper()}: {self.description}"


def get_operations_from_spec_dict(spec_dict) -> list[APIOperationDetails]:
    spec = OpenAPISpec.from_spec_dict(spec_dict)
    return get_operations_from_spec(spec, spec_dict)


def get_operations_from_spec(spec, spec_dict=None) -> list[APIOperationDetails]:
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
                    parameters=_extract_parameters(op, spec_dict, path, method),
                )
            )
    return operations


def _extract_parameters(operation: APIOperation, spec_dict=None, path: str = "", method: str = "") -> list[ParameterDetail]:
    """Extract parameter details from OpenAPI spec.

    Extracts both query/path parameters and request body parameters.
    Looks up parameter location (path, query, etc.) from the spec_dict.
    """
    # Build a map of parameter name -> param_in from the spec_dict
    param_in_map = {}
    if spec_dict and path and method:
        operation_spec = spec_dict.get("paths", {}).get(path, {}).get(method, {})
        for param_spec in operation_spec.get("parameters", []):
            param_name = param_spec.get("name")
            param_in = param_spec.get("in", "query")
            if param_name:
                param_in_map[param_name] = param_in

    parameters = []
    for property in operation.properties:
        param_in = param_in_map.get(property.name, "query")
        parameters.append(
            ParameterDetail(
                name=property.name,
                required=property.required,
                schema_type=property.type,
                description=property.description,
                default=property.default,
                param_in=param_in,
            )
        )

    # Extract request body parameters (these are always in the body)
    if operation.request_body:
        for param in operation.request_body.properties:
            params = param.dict()
            if isinstance(param.type, DataType):
                params["schema_type"] = param.type.value
                # UGLY HACK! DataType.Array is converted into a string like "Array<DataType.STRING>"
                # See langchain_community/tools/openapi/utils/api_models.py:315
            elif param.type.startswith("Array<"):
                params["schema_type"] = DataType.ARRAY.value
            params["param_in"] = "body"
            parameters.append(ParameterDetail.model_validate(params))

    return parameters
