"""The documented schema for the inspect response must match what it actually returns, rather than
falling back to the viewset's row serializer (the export ``ChatbotDetail`` serializer)."""

import pytest
from drf_spectacular.generators import SchemaGenerator


@pytest.fixture(scope="module")
def api_schema():
    # inspect is a v2-only endpoint; the generator filters per version, so ask for v2 explicitly.
    return SchemaGenerator(api_version="v2").get_schema(request=None, public=True)


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


def _resolve_ref(api_schema, ref: str) -> dict:
    """Resolve a local ``#/components/schemas/Name`` ref to its component."""
    return api_schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]


def _params_variants(api_schema) -> list[dict]:
    """The per-node-type param components behind ``InspectNode.params``.

    The field wraps the union in ``allOf``/``$ref`` (drf-spectacular does this when the field also
    carries a description), so follow the ref to the ``oneOf`` it points at.
    """
    schema = api_schema["components"]["schemas"]["InspectNode"]["properties"]["params"]
    if "allOf" in schema and len(schema["allOf"]) == 1:
        schema = schema["allOf"][0]
    if "$ref" in schema:
        schema = _resolve_ref(api_schema, schema["$ref"])
    assert "oneOf" in schema, "params should resolve to a oneOf of per-node-type param shapes"
    return [_resolve_ref(api_schema, entry["$ref"]) for entry in schema["oneOf"]]


def test_node_params_schema_is_polymorphic_per_node_type(api_schema):
    """``params`` is documented as a oneOf of per-node-type shapes, not an opaque object, so each
    node type's real parameters (and their meanings) are visible in the schema."""
    variants = _params_variants(api_schema)
    # Union of every documented param field across the node-type variants.
    documented_fields = {field for variant in variants for field in variant.get("properties", {})}

    # Type-specific params that only make sense on their own node type are now visible.
    assert {"template_string", "code", "keywords", "prompt", "tools"} <= documented_fields

    # Resource ids and the node label are surfaced under their own top-level keys, never in params.
    assert not ({"llm_provider_id", "source_material_id", "collection_id", "name"} & documented_fields)


def test_node_params_fields_carry_descriptions(api_schema):
    """The clarifying fields inside params describe themselves in the docs."""
    variants = _params_variants(api_schema)
    described = {
        field: spec.get("description") for variant in variants for field, spec in variant.get("properties", {}).items()
    }
    # ``keywords`` is router-only; its description should explain that, since it shows up empty on
    # legacy non-router nodes.
    assert described.get("keywords")
    assert described.get("history_type")


@pytest.mark.parametrize(
    ("component", "field"),
    [
        ("InspectGraphEdge", "source_handle"),
        ("InspectGraphEdge", "target_handle"),
        ("InspectSettings", "voice_response_behaviour"),
        ("ChatbotInspect", "is_unreleased"),
    ],
)
def test_clarifying_fields_carry_descriptions(api_schema, component, field):
    """Fields that don't stand on their own carry a description in the generated schema."""
    spec = api_schema["components"]["schemas"][component]["properties"][field]
    assert spec.get("description"), f"{component}.{field} should document itself"
