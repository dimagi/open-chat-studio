import enum
import json
from collections import defaultdict
from typing import Any

import httpx
from langchain.chains.openai_functions.openapi import _format_url
from langchain_community.tools import APIOperation
from langchain_community.utilities.openapi import OpenAPISpec
from langchain_core.tools import Tool, ToolException
from openapi_pydantic import DataType, Parameter, Reference, Schema
from pydantic import BaseModel, Field, create_model

from apps.service_providers.auth_service import AuthService


class FunctionDef(BaseModel):
    name: str
    description: str
    method: str
    url: str
    args_schema: type[BaseModel]

    def build_tool(self, auth_service) -> Tool:
        executor = OpenAPIOperationExecutor(auth_service, self)
        return Tool(
            name=self.name,
            description=self.description,
            executor=executor,
            args_schema=self.args_schema,
            handle_tool_error=True,
            func=executor.call_api,
        )


class OpenAPIOperationExecutor:
    def __init__(self, auth_service: AuthService, function_def: FunctionDef):
        self.auth_service = auth_service
        self.function_def = function_def

    def call_api(self, **kwargs) -> Any:
        method = self.function_def.method
        url = self.function_def.url
        path_params = kwargs.pop("path_params", {})
        url = _format_url(url, path_params)
        if "data" in kwargs and isinstance(kwargs["data"], dict):
            kwargs["data"] = json.dumps(kwargs["data"])

        with self.auth_service.get_http_client() as client:
            try:
                return self.auth_service.call_with_retries(self._make_request, client, url, method, **kwargs)
            except httpx.HTTPError as e:
                raise ToolException(f"Error making request: {str(e)}")

    def _make_request(self, http_client: httpx.Client, url: str, method: str, **kwargs) -> str:
        response = http_client.request(method.upper(), url, follow_redirects=False, **kwargs)
        response.raise_for_status()
        return response.text


def openapi_spec_op_to_function_def(spec: OpenAPISpec, path: str, method: str) -> FunctionDef:
    """
    Converts an OpenAPI operation to a Pydantic model.

    Args:
        spec (OpenAPISpec): The OpenAPI specification.
        path (str): The path of the operation.
        method (str): The HTTP method of the operation.

    Example Model:

        class GetWeatherModel(BaseModel):
            params: ParamsModel
            path_params: PathParamsModel
            headers: HeadersModel
            cookies: CookiesModel
            data: BodyModel
    """

    op = spec.get_operation(path, method)
    path_params = {(p.name, p.param_in): p for p in spec.get_parameters_for_path(path)}
    op_params = {(p.name, p.param_in): p for p in spec.get_parameters_for_operation(op)}

    # Group parameters by location
    params_by_type = defaultdict(list)
    for name_loc, p in {**path_params, **op_params}.items():
        params_by_type[name_loc[1]].append(p)

    # Get model for each parameter location
    request_args = {}
    param_loc_to_arg_name = {
        "query": "params",
        "header": "headers",
        "cookie": "cookies",
        "path": "path_params",
    }
    for param_loc, arg_name in param_loc_to_arg_name.items():
        if params_by_type[param_loc]:
            request_args[arg_name] = _openapi_params_to_pydantic_model(arg_name, params_by_type[param_loc], spec)

    # Get model for request body
    request_body = spec.get_request_body_for_operation(op)
    if request_body and request_body.content:
        if "application/json" in request_body.content:
            schema = spec.get_schema(request_body.content["application/json"].media_type_schema)
            schema.title = "body"
            request_args["data"] = _schema_to_pydantic_field_type(spec, schema)
        else:
            raise ValueError("Only application/json request bodies are supported")

    # Assemble final model
    api_op = APIOperation.from_openapi_spec(spec, path, method)
    function_name = api_op.operation_id
    args_schema = _create_model(
        function_name, {name: (type_, Field(...)) for name, type_ in request_args.items()}, __doc__=api_op.description
    )

    return FunctionDef(
        name=function_name,
        description=api_op.description,
        method=method,
        url=api_op.base_url + api_op.path,
        args_schema=args_schema,
    )


def _openapi_params_to_pydantic_model(name, params: list[Parameter], spec: OpenAPISpec) -> type[BaseModel]:
    """
    Converts OpenAPI parameters to a Pydantic model.

    Args:
        name (str): The name of the model.
        params (List[Parameter]): The list of OpenAPI parameters.
        spec (OpenAPISpec): The OpenAPI specification.

    Returns:
        dict: A dictionary representing the Pydantic model.
    """
    properties = {}
    required = []
    for p in params:
        if p.param_schema:
            schema = spec.get_schema(p.param_schema)
        else:
            media_type_schema = list(p.content.values())[0].media_type_schema  # type: ignore
            schema = spec.get_schema(media_type_schema)
        if p.name and not schema.title:
            schema.title = p.name
        if p.description and not schema.description:
            schema.description = p.description
        if p.required:
            required.append(p.name)
        properties[p.name] = _schema_to_pydantic(spec, schema)
    return _create_model(name, properties)


def _schema_to_pydantic(spec: OpenAPISpec, schema: Schema | Reference) -> tuple[type, Field]:
    """
    Converts an OpenAPI schema to a Pydantic field type.

    Args:
        spec (OpenAPISpec): The OpenAPI specification.
        schema (Schema | Reference): The schema to convert.

    Returns:
        tuple[type, Field]: A tuple containing the Pydantic field type and the field.
    """
    schema = _resolve(spec, schema)

    if schema.schema_if or schema.schema_not or schema.schema_else:
        raise ValueError("if, not, and else are not supported")

    if schema.allOf:
        if len(schema.allOf) > 1:
            raise ValueError("allOf with more than one schema is not supported")
        return _schema_to_pydantic(spec, _copy_meta(schema, _resolve(spec, schema.allOf[0])))
    elif schema.anyOf:
        if len(schema.anyOf) > 1:
            raise ValueError("anyOf with more than one schema is not supported")
        return _schema_to_pydantic(spec, _copy_meta(schema, _resolve(spec, schema.anyOf[0])))
    elif schema.oneOf:
        if len(schema.oneOf) > 1:
            raise ValueError("oneOf with more than one schema is not supported")
        return _schema_to_pydantic(spec, _copy_meta(schema, _resolve(spec, schema.oneOf[0])))

    field = Field(..., description=schema.description)
    return _schema_to_pydantic_field_type(spec, schema), field


def _schema_to_pydantic_field_type(spec: OpenAPISpec, schema: Schema) -> type:
    """
    Converts an OpenAPI schema to a Pydantic field type.

    The schema must be a single type schema (i.e. not a schema with anyOf, allOf, or oneOf) and must
    be resolved to its actual schema definition (i.e. not a reference).
    """
    if schema.type == DataType.OBJECT:
        if not schema.properties:
            return dict

        properties = {}
        for name, prop in schema.properties.items():
            if not prop.title:
                prop.title = name
            properties[name] = _schema_to_pydantic(spec, prop)
        return _create_model(schema.title, properties)
    elif schema.type == DataType.ARRAY:
        if not schema.items.title:
            schema.items.title = f"{schema.title}Items"
        return list[_schema_to_pydantic(spec, schema.items)]
    elif schema.enum:
        return _get_enum_type(schema)
    else:
        return _get_basic_type(schema.type)


def _get_enum_type(schema) -> type[enum.Enum]:
    if schema.type == DataType.STRING:
        type_ = enum.StrEnum(_make_model_name(schema.title, "Enum"), [(v, v) for v in schema.enum if v])
        type_.__doc__ = schema.description
        return type_
    else:
        raise ValueError(f"Unsupported enum type: {schema.type}")


def _get_basic_type(data_type: DataType) -> type:
    if data_type == DataType.STRING:
        return str
    elif data_type == DataType.INTEGER:
        return int
    elif data_type == DataType.NUMBER:
        return float
    elif data_type == DataType.BOOLEAN:
        return bool
    else:
        raise ValueError(f"Unsupported type: {data_type}")


def _create_model(name, properties, **kwargs) -> type[BaseModel]:
    return create_model(_make_model_name(name), **properties, **kwargs)


def _make_model_name(name, suffix="Model"):
    name = name.title().replace("-", "").replace("_", "")
    return f"{name}{suffix}"


def _copy_meta(source, target):
    """Copy metadata from source to target schema to preserve metadata."""
    if source.title and not target.title:
        target.title = source.title
    if source.description and not target.description:
        target.description = source.description
    return target


def _resolve(spec, schema):
    """Resolves a schema reference to its actual schema definition."""
    if isinstance(schema, Reference):
        schema = spec.get_referenced_schema(schema)
    return schema
