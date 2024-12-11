import pytest
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from apps.custom_actions.forms import validate_api_schema, validate_api_schema_full


class TestValidateApiSchema:
    def test_invalid_schema(self):
        with pytest.raises(ValidationError, match="Invalid OpenAPI schema."):
            validate_api_schema({"paths": {}})

    def test_valid_schema(self):
        validate_api_schema(_make_openapi_schema({"/test": {"get": {}}}))

    def test_missing_paths(self):
        with pytest.raises(ValidationError, match="No paths found in the schema."):
            validate_api_schema(_make_openapi_schema({}))

    def test_invalid_path_format(self):
        with pytest.raises(ValidationError, match="Invalid path: invalid path"):
            validate_api_schema_full(
                ["invalid_path_get"],
                _make_openapi_schema({"invalid path": {"get": {}}}),
                "http://example.com",
                URLValidator(schemes=["https"]),
            )

    def test_malformed_server_url(self):
        with pytest.raises(ValidationError, match="Invalid path: /test"):
            validate_api_schema_full(
                ["test_get"],
                _make_openapi_schema({"/test": {"get": {}}}),
                "https://not a url",
                URLValidator(schemes=["https"]),
            )


def _make_openapi_schema(paths: dict):
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "servers": [{"url": "https://example.com"}],
        "paths": paths,
    }
