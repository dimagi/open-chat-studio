"""Tests for the per-resource sync endpoint documentation: each synced resource is mounted at its
own literal path (so OpenAPI can give it a distinct response schema) and routed to a ResourceView
subclass carrying that schema."""

import pytest
from django.urls import resolve
from drf_spectacular.generators import SchemaGenerator

from apps.api.export.serializers import component_name, resource_responses
from apps.api.export.views import ResourceView, resource_view
from apps.teams.export.manifest import MANIFEST_ENTRIES, entry_model, get_manifest_entry


def test_each_resource_resolves_to_a_resourceview_carrying_its_name():
    for entry in MANIFEST_ENTRIES:
        match = resolve(f"/api/export/{entry.resource}/")
        assert issubclass(match.func.cls, ResourceView)
        assert match.kwargs == {"resource": entry.resource}


def test_resource_view_class_name_uses_the_component_name():
    """The generated view's class name is PascalCased and space-free (e.g. 'ChatbotResourceView'),
    matching how the rest of the export subsystem names things off the model's verbose_name."""
    for entry in MANIFEST_ENTRIES:
        expected = f"{component_name(entry_model(entry.model))}ResourceView"
        assert resource_view(entry).__name__ == expected


def test_unknown_resource_resolves_to_catchall_view():
    # A catch-all route lets the view return a JSON 404 rather than Django's HTML 404.
    match = resolve("/api/export/not_a_real_resource/")
    assert issubclass(match.func.cls, ResourceView)
    assert match.kwargs == {"resource": "not_a_real_resource"}


@pytest.mark.parametrize(
    ("resource", "expect_409"),
    [
        pytest.param("llm_providers", True, id="secret-resource-documents-409"),
        pytest.param("users", False, id="plain-resource-has-no-409"),
    ],
)
def test_secret_resources_document_the_no_public_key_conflict(resource, expect_409):
    # Routing prevents unknown resources from reaching the view, so 404 is not a documented response.
    statuses = resource_responses(get_manifest_entry(resource))
    assert 200 in statuses
    assert 404 not in statuses
    assert (409 in statuses) is expect_409


@pytest.fixture(scope="module")
def export_components():
    return SchemaGenerator(api_version="export").get_schema(request=None, public=True)["components"]["schemas"]


@pytest.fixture(scope="module")
def v2_components():
    return SchemaGenerator(api_version="v2").get_schema(request=None, public=True)["components"]["schemas"]


def test_every_export_resource_publishes_its_row_component(export_components):
    """Each synced resource's documented row serializer is published as a valid, space-free
    '<Model>Detail' component in the standalone export schema -- secret resources included, since
    their secret fields are redeclared as sealed strings on that same serializer. The user-facing
    rename flows through (Experiment -> 'ChatbotDetail')."""
    missing = []
    for entry in MANIFEST_ENTRIES:
        expected = f"{component_name(entry_model(entry.model))}Detail"
        if expected not in export_components:
            missing.append(expected)
    assert not missing, f"export resources without a row component: {missing}"


@pytest.mark.parametrize("model_name", ["Team", "ConsentForm", "SourceMaterial"])
def test_export_detail_and_curated_serializer_live_in_separate_schemas(export_components, v2_components, model_name):
    """The export surface is its own schema, so the export '<Model>Detail' row serializer lives only
    in the export schema while the curated public/inspect serializer (the bare model name) stays in
    v2. The '<Model>Detail' naming keeps them distinct, and the export Detail never leaks into v2."""
    assert f"{model_name}Detail" in export_components, f"export '{model_name}Detail' missing"
    assert model_name in v2_components, f"curated '{model_name}' component went missing from v2"
    assert f"{model_name}Detail" not in v2_components, f"export '{model_name}Detail' leaked into v2"
