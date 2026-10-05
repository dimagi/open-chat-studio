import re
from typing import Any, Literal

import pytest
from pydantic import Field, create_model

from apps.utils.schema_utils import (
    collapse_optional_types,
    create_model_with_sanitized_names,
    resolve_references,
    sanitize_property_name,
)


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

    with pytest.raises(
        ValueError, match=re.escape("External references are not supported: http://example.com/definitions/Pet")
    ):
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


def test_a_reference_cycle_terminates_rather_than_recursing():
    """Substitution is one level deep: a `$ref` inside a substituted target is left alone, so a
    schema that points back at itself has nothing to loop on. Resolving deeply instead would need a
    visited set to terminate here, and would inline a caller's whole spec into every reference to
    it -- `resolve_references` runs over user-supplied OpenAPI specs in `apps/custom_actions`."""
    spec = {
        "definitions": {"Pet": {"type": "object", "properties": {"friend": {"$ref": "#/definitions/Pet"}}}},
        "paths": {"/pets": {"get": {"responses": {"200": {"$ref": "#/definitions/Pet"}}}}},
    }

    resolved = resolve_references(spec)

    pet = resolved["paths"]["/pets"]["get"]["responses"]["200"]
    assert pet["type"] == "object"
    assert pet["properties"]["friend"] == {"$ref": "#/definitions/Pet"}


def _schema_for(annotation) -> dict:
    """The schema pydantic writes for a model holding one optional field of this type."""
    return resolve_references(create_model("Probe", field=(annotation, None)).model_json_schema())


@pytest.mark.parametrize(
    ("annotation", "expected_type"),
    [
        pytest.param(int | None, "integer", id="int"),
        pytest.param(str | None, "string", id="str"),
        pytest.param(list[str] | None, "array", id="list"),
        pytest.param(dict[str, int] | None, "object", id="dict"),
        pytest.param(Literal["a", "b"] | None, "string", id="literal-of-one-type"),
    ],
)
def test_an_optional_field_collapses_to_the_type_it_holds(annotation, expected_type):
    """Pydantic writes every optional field as an `anyOf` against `null`, which tells a client
    nothing `required` hasn't already told it. What the client needs is the one type it may send."""
    schema = _schema_for(annotation)

    collapse_optional_types(schema)

    assert schema["properties"]["field"]["type"] == expected_type
    assert "anyOf" not in schema["properties"]["field"]


@pytest.mark.parametrize(
    "annotation",
    [
        pytest.param(Any | None, id="a-member-carrying-no-type-at-all"),
        pytest.param(Literal["a", 1] | None, id="an-enum-whose-values-span-two-types"),
        pytest.param(list[str] | int | None, id="two-non-null-members-either-of-which-is-valid"),
    ],
)
def test_a_union_with_no_single_type_is_left_as_pydantic_wrote_it(annotation):
    """There is no one type to collapse to, so the `anyOf` stands. Naming one member's type would
    rule out values the field accepts, and the member may carry no `type` to read in the first
    place -- which used to raise a `KeyError` and take the whole schema down with it."""
    schema = _schema_for(annotation)

    collapse_optional_types(schema)

    assert "anyOf" in schema["properties"]["field"]
    assert "type" not in schema["properties"]["field"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("score", "score", id="already-valid-is-untouched"),
        pytest.param("test__id__get", "test__id__get", id="valid-double-underscore-is-untouched"),
        pytest.param("X-Custom-Header", "X-Custom-Header", id="hyphens-are-untouched"),
        pytest.param("score (1-5)", "score_1-5_", id="spaces-and-parens-become-underscores"),
        pytest.param("my field", "my_field", id="single-space-becomes-underscore"),
        pytest.param("a/b\\c", "a_b_c", id="slashes-become-underscores"),
        pytest.param("!!!", "field", id="all-invalid-chars-fall-back-to-field"),
        pytest.param("", "field", id="empty-string-falls-back-to-field"),
        pytest.param("a" * 100, "a" * 64, id="over-64-chars-is-truncated"),
        # `pydantic.create_model` rejects a field name starting with `_` outright, even one that's
        # otherwise a legal property name -- see
        # `test_create_model_with_sanitized_names_accepts_names_with_leading_invalid_characters`.
        pytest.param("$filter", "filter", id="leading-invalid-char-does-not-leave-a-leading-underscore"),
        pytest.param("(score)", "score_", id="leading-and-trailing-invalid-chars"),
        pytest.param(" score", "score", id="leading-space-does-not-leave-a-leading-underscore"),
        pytest.param("_score", "score", id="a-name-already-leading-with-underscore-is-stripped"),
        pytest.param("___", "field", id="all-underscores-falls-back-to-field"),
    ],
)
def test_sanitize_property_name(name, expected):
    assert sanitize_property_name(name) == expected


def test_sanitize_property_name_is_idempotent():
    """Anthropic's pattern allows `_`, so re-sanitizing an already-sanitized name must be a no-op --
    otherwise a caller that sanitizes twice (e.g. once per provider) would keep reshaping it."""
    once = sanitize_property_name("score (1-5)!!")
    assert sanitize_property_name(once) == once


def test_sanitize_property_name_avoids_collisions_when_truncating_or_replacing():
    """Two distinct field names that sanitize to the same string must not collapse into one --
    that would silently drop a field from a dynamically-built schema."""
    taken: set[str] = set()

    first = sanitize_property_name("my field!", taken)
    taken.add(first)
    second = sanitize_property_name("my field?", taken)
    taken.add(second)

    assert first != second
    assert {first, second} == taken


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("$filter", id="odata-style-query-param"),
        pytest.param("(score)", id="leading-and-trailing-invalid-chars"),
        pytest.param(" score", id="leading-space"),
        pytest.param("_score", id="name-already-leads-with-underscore"),
    ],
)
def test_create_model_with_sanitized_names_accepts_names_with_leading_invalid_characters(name):
    """`sanitize_property_name` alone isn't the whole story: `pydantic.create_model` raises
    `NameError` for a field name starting with `_`, a stricter rule than the Anthropic pattern
    checks for. A raw name whose first character is invalid (e.g. an OData query param like
    `$filter`, standard on Microsoft Graph) used to substitute to exactly that -- so this exercises
    the real `create_model` call `sanitize_property_name`'s own tests can't, and round-trips the
    original name back out through `model_dump()`."""
    model = create_model_with_sanitized_names("Probe", {name: (str, Field(...))})
    field_name = next(iter(model.model_fields))

    assert not field_name.startswith("_")
    assert model(**{field_name: "value"}).model_dump() == {name: "value"}
