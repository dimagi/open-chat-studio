import enum
import logging
import pathlib
import tempfile
import uuid
from collections import defaultdict
from email.message import Message
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import httpx
from django.conf import settings
from langchain_classic.chains.openai_functions.openapi import _format_url
from langchain_community.tools import APIOperation
from langchain_community.utilities.openapi import OpenAPISpec
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from openapi_pydantic import DataType, Parameter, Reference, Schema
from pydantic import BaseModel, Field, create_model

from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.utils import create_notification
from apps.service_providers.auth_service import AuthService
from apps.utils.urlvalidate import InvalidURL, validate_user_input_url

if TYPE_CHECKING:
    from apps.custom_actions.models import CustomAction

logger = logging.getLogger("ocs.tools")


class ToolArtifact(BaseModel):
    name: str
    content_type: str
    content: bytes = None
    path: str = None

    def get_content(self):
        if self.content:
            return self.content
        return pathlib.Path(self.path).read_bytes()


class FunctionDef(BaseModel):
    """
    Represents a function definition for an OpenAPI operation.
    """

    name: str
    description: str
    method: str
    url: str
    args_schema: type[BaseModel]

    def build_tool(self, auth_service: AuthService, custom_action: "CustomAction" = None) -> BaseTool:
        executor = OpenAPIOperationExecutor(auth_service, self, custom_action)
        func = executor.call_api_with_notifications if custom_action else executor.call_api
        return StructuredTool(
            name=self.name,
            description=self.description,
            args_schema=self.args_schema,
            handle_tool_error=True,
            func=func,
            response_format="content_and_artifact",
        )


class OpenAPIOperationExecutor:
    def __init__(self, auth_service: AuthService, function_def: FunctionDef, custom_action: "CustomAction" = None):
        self.auth_service = auth_service
        self.function_def = function_def
        self.custom_action = custom_action

    def call_api(self, **kwargs) -> Any:
        """Make an HTTP request to an external service. The exact inputs to this function are the
        parameters defined in the OpenAPI spec, but we expect the following kwargs:

        params: query params
        path_params: path params
        headers: headers
        cookies: cookies
        body_data: request body

        All of these will be pydantic models.

        See `openapi_spec_op_to_function_def` for how these models are generated.
        """
        method = self.function_def.method
        path_params = kwargs.pop("path_params", None)

        if "body_data" in kwargs:
            kwargs["json"] = kwargs.pop("body_data")

        kwargs = {k: v.model_dump() if isinstance(v, BaseModel) else v for k, v in kwargs.items()}

        url = self._get_url(path_params)
        with self.auth_service.get_http_client() as client:
            try:
                return self.auth_service.call_with_retries(self._make_request, client, url, method, **kwargs)
            except httpx.HTTPStatusError as e:
                if e.response and e.response.status_code == 400:
                    raise ToolException(f"Bad request: {e.response.text}") from None
                raise ToolException(f"Error making request: {str(e)}") from None
            except httpx.HTTPError as e:
                raise ToolException(f"Error making request: {str(e)}") from None

    def call_api_with_notifications(self, **kwargs) -> Any:
        """Wrapper around call_api that creates notifications for monitoring custom action health.

        This wrapper tracks:
        - ERROR: API failures, timeouts, bad responses
        """

        try:
            result = self.call_api(**kwargs)
            return result

        except ToolException as e:
            self._create_api_failure_notification(e)
            raise
        except Exception as e:
            self._create_unexpected_error_notification(e)
            raise

    def _create_api_failure_notification(self, exception: Exception) -> None:
        """Create notification for API failures."""
        method = self.function_def.method.upper()
        operation = self.function_def.name
        create_notification(
            title=f"Custom Action '{self.custom_action.name}' failed",
            message=f"{method} '{operation}' API call failed: {exception}",
            level=LevelChoices.ERROR,
            team=self.custom_action.team,
            permissions=["custom_actions.view_customaction"],
            slug="custom-action-api-failure",
            event_data={"action_id": self.custom_action.id, "exception_type": type(exception).__name__},
        )

    def _create_unexpected_error_notification(self, exception: Exception) -> None:
        """Create notification for unexpected errors."""
        method = self.function_def.method.upper()
        operation = self.function_def.name

        create_notification(
            title=f"Custom Action '{self.custom_action.name}' encountered an error",
            message=f"{method} '{operation}' failed with an unexpected error: {exception}",
            level=LevelChoices.ERROR,
            team=self.custom_action.team,
            permissions=["custom_actions.view_customaction"],
            slug="custom-action-unexpected-error",
            event_data={"action_id": self.custom_action.id, "exception_type": type(exception).__name__},
        )

    def _make_request(
        self, http_client: httpx.Client, url: str, method: str, **kwargs
    ) -> tuple[str, ToolArtifact | None]:
        logger.info("[%s] %s %s", self.function_def.name, method.upper(), url)
        with http_client.stream(method.upper(), url, follow_redirects=False, **kwargs) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                response.read()  # read the response to get the error message
                raise
            if content_disposition := response.headers.get("content-disposition"):
                filename = self._get_filename_from_header(content_disposition)
                if filename:
                    logger.info("[%s] response with attachment: %s", self.function_def.name, filename)
                    return self._get_artifact_response(content_disposition, filename, response)

            logger.info("[%s] response with content", self.function_def.name)
            response.read()
            return response.text, None

    def _get_artifact_response(self, content_disposition, filename, response):
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) < 1000**2:
            # just load it into memory if it's < 1MB
            response.read()
            return content_disposition, ToolArtifact(content=response.content, name=filename, content_type=content_type)
        # otherwise stream it to disk
        with tempfile.NamedTemporaryFile(delete=False) as f:
            for chunk in response.iter_bytes():
                f.write(chunk)
            f.close()
            return content_disposition, ToolArtifact(path=f.name, name=filename, content_type=content_type)

    def _get_filename_from_header(self, content_disposition):
        """Return the attachment filename or None if the content-disposition header is not an attachment."""
        try:
            msg = Message()
            msg["content-disposition"] = content_disposition
            if msg.get_content_disposition() != "attachment":
                return

            filename = msg.get_filename()
        except Exception as e:
            raise ToolException(f"Invalid content-disposition header: {str(e)}") from e
        return filename or str(uuid.uuid4())

    def _get_url(self, path_params):
        url = self.function_def.url
        if path_params:
            url = _format_url(url, path_params.model_dump())

        try:
            validate_user_input_url(url, strict=not settings.DEBUG)
        except InvalidURL as e:
            raise ToolException(str(e)) from None

        return url


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
            body_data: BodyModel
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
            # note: This was changed from 'data' because it seems to work better :shrug:
            schema.title = "body_data"
            request_args["body_data"] = _schema_to_pydantic_field_type(spec, schema)
        else:
            raise ValueError("Only application/json request bodies are supported")

    # Assemble final model
    api_op = APIOperation.from_openapi_spec(spec, path, method)
    function_name = api_op.operation_id
    args_schema = _create_model(
        function_name, {name: (type_, Field(...)) for name, type_ in request_args.items()}, __doc__=api_op.description
    )

    url = urljoin(api_op.base_url, api_op.path)
    return FunctionDef(
        name=function_name,
        description=api_op.description,
        method=method,
        url=url,
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
