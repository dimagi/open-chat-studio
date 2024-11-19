import pytest

from apps.custom_actions.schema_utils import resolve_references


def test_resolve_simple_reference():
    openapi_spec = {
        "definitions": {
            "Pet": {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}
        },
        "paths": {"/pets": {"get": {"responses": {"200": {"$ref": "#/definitions/Pet"}}}}},
    }

    resolved_spec = resolve_references(openapi_spec)
    assert resolved_spec["paths"]["/pets"]["get"]["responses"]["200"] == openapi_spec["definitions"]["Pet"]


def test_resolve_nested_reference():
    openapi_spec = {
        "definitions": {
            "Pet": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "owner": {"$ref": "#/definitions/Person"},
                },
            },
            "Person": {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}},
        },
        "paths": {"/pets": {"get": {"responses": {"200": {"$ref": "#/definitions/Pet"}}}}},
    }

    resolved_spec = resolve_references(openapi_spec)
    assert resolved_spec["definitions"]["Pet"]["properties"]["owner"] == openapi_spec["definitions"]["Person"]


def test_resolve_multiple_references():
    openapi_spec = {
        "definitions": {
            "Pet": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "owner": {"$ref": "#/definitions/Person"},
                },
            },
            "Person": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "pets": {"type": "array", "items": {"$ref": "#/definitions/Pet"}},
                },
            },
        },
        "paths": {
            "/pets": {"get": {"responses": {"200": {"$ref": "#/definitions/Pet"}}}},
            "/people": {"get": {"responses": {"200": {"$ref": "#/definitions/Person"}}}},
        },
    }

    resolved_spec = resolve_references(openapi_spec)
    assert resolved_spec["definitions"]["Person"]["properties"]["pets"]["items"] == openapi_spec["definitions"]["Pet"]
    assert resolved_spec["paths"]["/pets"]["get"]["responses"]["200"] == openapi_spec["definitions"]["Pet"]
    assert resolved_spec["paths"]["/people"]["get"]["responses"]["200"] == openapi_spec["definitions"]["Person"]


def test_basic_types():
    spec = {
        "definitions": {
            "PetExample": {
                "type": "object",
                "properties": {
                    "id": 1,
                    "name": "Dog",
                },
            }
        },
        "examples": [
            {"$ref": "#/definitions/PetExample"},
            {"id": 2, "name": "Cat"},
        ],
    }
    resolved_spec = resolve_references(spec)
    assert resolved_spec["examples"][0] == spec["definitions"]["PetExample"]


def test_external_reference():
    spec = {
        "pet": {"$ref": "http://example.com/definitions/Pet"},
    }

    with pytest.raises(ValueError, match="External references are not supported: http://example.com/definitions/Pet"):
        resolve_references(spec)


def test_preserve_description():
    spec = {
        "definitions": {
            "PetExample": {
                "type": "object",
                "properties": {
                    "id": 1,
                    "name": "Dog",
                },
            }
        },
        "examples": [
            {"$ref": "#/definitions/PetExample", "description": "An example of a pet"},
            {"id": 2, "name": "Cat"},
        ],
    }
    resolved_spec = resolve_references(spec)
    assert resolved_spec["examples"][0]["description"] == "An example of a pet"
