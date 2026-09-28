import pytest
from openapi_pydantic import Reference

from apps.utils.openapi import OpenAPISpec


def _spec(paths=None, **extra):
    return {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": paths or {}, **extra}


class TestFromSpecDict:
    def test_rejects_document_without_openapi_or_swagger_key(self):
        with pytest.raises(ValueError, match="not an OpenAPI"):
            OpenAPISpec.from_spec_dict({"info": {"title": "t", "version": "1"}, "paths": {}})

    def test_missing_required_top_level_field_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid OpenAPI spec"):
            OpenAPISpec.from_spec_dict({"openapi": "3.0.0", "paths": {"/a": {"get": {}}}})

    def test_invalid_operation_field_is_dropped(self):
        spec = OpenAPISpec.from_spec_dict(_spec({"/a": {"get": {"operationId": 5, "summary": "s"}}}))
        operation = spec.get_operation("/a", "get")
        assert operation.operationId is None
        assert operation.summary == "s"

    def test_invalid_parameter_is_dropped(self):
        params = [{"name": "bad", "in": "nowhere"}, {"name": "ok", "in": "query"}]
        spec = OpenAPISpec.from_spec_dict(_spec({"/a": {"get": {"parameters": params}}}))
        operation = spec.get_operation("/a", "get")
        assert [p.name for p in spec.get_parameters_for_operation(operation)] == ["ok"]

    def test_invalid_body_property_leaf_is_dropped(self):
        body_schema = {"type": "object", "properties": {"ok": {"type": "string"}, "bad": {"type": "file"}}}
        request_body = {"content": {"application/json": {"schema": body_schema}}}
        spec = OpenAPISpec.from_spec_dict(_spec({"/a": {"post": {"requestBody": request_body}}}))
        body = spec.get_request_body_for_operation(spec.get_operation("/a", "post"))
        assert body is not None
        schema = spec.get_schema(body.content["application/json"].media_type_schema)
        assert schema.properties is not None
        assert list(schema.properties) == ["ok", "bad"]
        assert schema.properties["ok"].type == "string"
        assert schema.properties["bad"].type is None

    def test_invalid_required_parameter_raises(self):
        params = [{"name": "x", "in": "nowhere", "required": True, "schema": {"type": "string"}}]
        expected = r"Invalid OpenAPI spec: Input should be .* at paths//a/get/parameters/0/Parameter/in"
        with pytest.raises(ValueError, match=expected):
            OpenAPISpec.from_spec_dict(_spec({"/a": {"get": {"parameters": params}}}))

    def test_required_swagger_body_parameter_raises(self):
        spec_dict = {
            "swagger": "2.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/pet": {
                    "put": {
                        "operationId": "updatePet",
                        "parameters": [{"name": "body", "in": "body", "required": True, "schema": {"type": "object"}}],
                    }
                }
            },
        }
        with pytest.raises(ValueError, match="Invalid OpenAPI spec"):
            OpenAPISpec.from_spec_dict(spec_dict)

    def test_request_body_without_content_raises(self):
        with pytest.raises(ValueError, match="Invalid OpenAPI spec"):
            OpenAPISpec.from_spec_dict(_spec({"/a": {"post": {"requestBody": {"required": True}}}}))

    def test_invalid_parts_are_removed_in_batches(self, monkeypatch):
        validations = []
        validate = OpenAPISpec.model_validate

        def counting_validate(obj):
            validations.append(obj)
            return validate(obj)

        monkeypatch.setattr(OpenAPISpec, "model_validate", counting_validate)
        params = [{"name": f"p{i}", "in": "nowhere"} for i in range(400)] + [{"name": "ok", "in": "query"}]
        spec = OpenAPISpec.from_spec_dict(_spec({"/a": {"get": {"parameters": params}}}))
        assert len(validations) <= 3
        operation = spec.get_operation("/a", "get")
        assert [p.name for p in spec.get_parameters_for_operation(operation)] == ["ok"]

    def test_does_not_mutate_input(self):
        spec_dict = _spec({"/a": {"get": {"operationId": 5}}})
        OpenAPISpec.from_spec_dict(spec_dict)
        assert spec_dict["paths"]["/a"]["get"]["operationId"] == 5


@pytest.mark.parametrize(
    ("servers", "expected"),
    [
        pytest.param([{"url": "https://api.example.com"}], "https://api.example.com", id="declared"),
        pytest.param(None, "/", id="missing"),
    ],
)
def test_base_url(servers, expected):
    extra = {"servers": servers} if servers else {}
    assert OpenAPISpec.from_spec_dict(_spec(**extra)).base_url == expected


def test_get_methods_for_path_uses_http_verb_order():
    spec = OpenAPISpec.from_spec_dict(_spec({"/a": {"post": {}, "get": {}, "delete": {}}}))
    assert spec.get_methods_for_path("/a") == ["get", "post", "delete"]


def test_get_operation_errors():
    spec = OpenAPISpec.from_spec_dict(_spec({"/test": {"get": {}}}))
    with pytest.raises(ValueError, match="No path found for /missing"):
        spec.get_operation("/missing", "get")
    with pytest.raises(ValueError, match="No delete method found for /test"):
        spec.get_operation("/test", "delete")


def test_parameter_and_request_body_references_are_resolved():
    spec = OpenAPISpec.from_spec_dict(
        _spec(
            {
                "/a": {
                    "parameters": [{"$ref": "#/components/parameters/Shared"}],
                    "post": {
                        "parameters": [{"$ref": "#/components/parameters/Limit"}],
                        "requestBody": {"$ref": "#/components/requestBodies/Body"},
                    },
                }
            },
            components={
                "parameters": {
                    "Limit": {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                    "Shared": {"name": "trace", "in": "header", "schema": {"type": "string"}},
                },
                "requestBodies": {
                    "Body": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Thing"}}}}
                },
                "schemas": {"Thing": {"type": "object", "properties": {"name": {"type": "string"}}}},
            },
        )
    )
    operation = spec.get_operation("/a", "post")
    assert [p.name for p in spec.get_parameters_for_operation(operation)] == ["limit"]
    assert [p.name for p in spec.get_parameters_for_path("/a")] == ["trace"]
    body = spec.get_request_body_for_operation(operation)
    schema = spec.get_schema(body.content["application/json"].media_type_schema)
    assert schema.properties is not None
    assert list(schema.properties) == ["name"]
    assert not isinstance(schema.properties["name"], Reference)


@pytest.mark.parametrize(
    ("operation_id", "path", "method", "expected"),
    [
        pytest.param("getWeather", "/weather", "get", "getWeather", id="plain"),
        pytest.param("get-weather.v2/now", "/weather", "get", "get_weather_v2_now", id="punctuation"),
        pytest.param(None, "/a-b/c.d", "get", "a_b_c_d_get", id="from-path"),
        pytest.param(None, "/users/{id}", "post", "users__id__post", id="from-path-template"),
    ],
)
def test_get_cleaned_operation_id(operation_id, path, method, expected):
    operation = {"operationId": operation_id} if operation_id else {}
    spec = OpenAPISpec.from_spec_dict(_spec({path: {method: operation}}))
    assert spec.get_cleaned_operation_id(spec.get_operation(path, method), path, method) == expected


@pytest.mark.parametrize(
    ("path_item", "expected"),
    [
        pytest.param({"get": {"description": "D", "summary": "S"}}, "D", id="operation-description"),
        pytest.param({"get": {"summary": "S"}}, "S", id="operation-summary"),
        pytest.param({"description": "PD", "get": {}}, "PD", id="path-description"),
        pytest.param({"summary": "PS", "get": {}}, "PS", id="path-summary"),
        pytest.param({"get": {}}, "", id="none"),
    ],
)
def test_get_operation_description(path_item, expected):
    spec = OpenAPISpec.from_spec_dict(_spec({"/a": path_item}))
    assert spec.get_operation_description("/a", spec.get_operation("/a", "get")) == expected
