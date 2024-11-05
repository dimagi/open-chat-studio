import pytest
from django.core.exceptions import ValidationError

from apps.custom_actions.forms import validate_api_schema


class TestValidateApiSchema:
    def test_invalid_schema(self):
        with pytest.raises(ValidationError, match="Invalid OpenAPI schema."):
            validate_api_schema({"paths": {}})

    def test_missing_server(self):
        schema = _make_openapi_schema({"/test": {}})
        schema["servers"] = []
        with pytest.raises(ValidationError, match="No servers found in the schema."):
            validate_api_schema(schema)

    def test_multiple_servers(self):
        schema = _make_openapi_schema({"/test": {}})
        schema["servers"].append({"url": "https://example1.com"})
        with pytest.raises(ValidationError, match="Multiple servers found in the schema. Only one is allowed."):
            validate_api_schema(schema)

    def test_valid_schema(self):
        validate_api_schema(_make_openapi_schema({"/test": {}}, server_url="https://example.com"))

    def test_invalid_server_url(self):
        with pytest.raises(
            ValidationError, match="The server URL is invalid. Ensure that the URL is a valid HTTPS URL"
        ):
            validate_api_schema(_make_openapi_schema({"/test": {}}, server_url="http://example.com"))

    def test_missing_paths(self):
        with pytest.raises(ValidationError, match="No paths found in the schema."):
            validate_api_schema(_make_openapi_schema({}, server_url="https://example.com"))

    def test_invalid_path_format(self):
        with pytest.raises(ValidationError, match="Invalid path: invalid path"):
            validate_api_schema(_make_openapi_schema({"invalid path": {}}))

    def test_malformed_server_url(self):
        with pytest.raises(
            ValidationError, match="The server URL is invalid. Ensure that the URL is a valid HTTPS URL"
        ):
            validate_api_schema(_make_openapi_schema({"/test": {}}, server_url="https://not a url"))


def _make_openapi_schema(paths: dict, server_url="https://example.com"):
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": server_url}],
        "paths": paths,
    }
