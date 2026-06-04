"""Regression: the documented inspect response schema must reflect the actual payload, not fall
back to the viewset's ``ChatbotSerializer``."""

import pytest
from drf_spectacular.generators import SchemaGenerator


@pytest.fixture(scope="module")
def api_schema():
    return SchemaGenerator().get_schema(request=None, public=True)


def test_inspect_200_references_the_inspect_component(api_schema):
    operation = api_schema["paths"]["/api/v2/chatbots/{id}/inspect/"]["get"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref == "#/components/schemas/ChatbotInspect"


def test_inspect_component_documents_the_payload_envelope(api_schema):
    component = api_schema["components"]["schemas"]["ChatbotInspect"]
    assert set(component["properties"]) == {
        "id",
        "name",
        "description",
        "version_number",
        "is_unreleased",
        "is_published_version",
        "version_description",
        "team_slug",
        "settings",
        "consent_form",
        "voice",
        "trace_provider",
        "channels",
        "pipeline",
        "events",
    }


def test_node_component_declares_reference_keys(api_schema):
    node = api_schema["components"]["schemas"]["InspectNode"]
    assert {"node_id", "type", "label", "params"} <= set(node["required"])
    assert {
        "llm",
        "voice",
        "source_material",
        "assistant",
        "custom_actions",
        "media_collection",
        "indexed_collections",
    } <= set(node["properties"])
