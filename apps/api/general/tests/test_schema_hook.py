"""Tests for the per-resource sync endpoint documentation: each synced resource is mounted at its
own literal path (so OpenAPI can give it a distinct response schema) and routed to a ResourceView
subclass carrying that schema."""

import pytest
from django.urls import resolve
from drf_spectacular.generators import SchemaGenerator
from rest_framework import serializers

from apps.api.general.serializers import (
    ManifestEntrySerializer,
    ManifestSerializer,
    build_resource_response_serializer,
    build_resource_serializer,
    component_name,
    resource_responses,
)
from apps.api.general.views import ResourceView, resource_view
from apps.teams.export.manifest import MANIFEST_ENTRIES, build_manifest, entry_model, get_manifest_entry


def test_each_resource_resolves_to_a_resourceview_carrying_its_name():
    for entry in MANIFEST_ENTRIES:
        match = resolve(f"/api/v2/team/{entry.resource}/")
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
    match = resolve("/api/v2/team/not_a_real_resource/")
    assert issubclass(match.func.cls, ResourceView)
    assert match.kwargs == {"resource": "not_a_real_resource"}


def test_response_envelope_has_cursor_has_more_and_results():
    fields = build_resource_response_serializer(get_manifest_entry("users"))().fields
    assert set(fields) == {"cursor", "has_more", "results"}
    assert isinstance(fields["results"], serializers.ListSerializer)


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


def test_secret_fields_documented_as_sealed_strings():
    # llm_providers' `config` is sealed to a base64 string at runtime, not its raw model type.
    model = entry_model(get_manifest_entry("llm_providers").model)
    fields = build_resource_serializer(model)().fields
    assert isinstance(fields["config"], serializers.CharField)


@pytest.mark.django_db()
def test_manifest_serializer_matches_build_manifest_payload():
    """The documented manifest response must stay in step with what the view actually returns."""
    manifest = build_manifest()
    assert set(ManifestSerializer().fields) == set(manifest)
    declared = set(ManifestEntrySerializer().fields)
    actual_keys = set().union(*(entry.keys() for entry in manifest["entries"]))
    assert declared == actual_keys


@pytest.fixture(scope="module")
def v2_components():
    return SchemaGenerator(api_version="v2").get_schema(request=None, public=True)["components"]["schemas"]


def test_every_export_resource_publishes_its_row_component(v2_components):
    """Each synced resource's documented row serializer is published as a valid, space-free
    '<Model>Detail' component -- secret resources included, since their secret fields are redeclared
    as sealed strings on that same serializer. The user-facing rename flows through (Experiment ->
    'ChatbotDetail')."""
    missing = []
    for entry in MANIFEST_ENTRIES:
        expected = f"{component_name(entry_model(entry.model))}Detail"
        if expected not in v2_components:
            missing.append(expected)
    assert not missing, f"export resources without a row component: {missing}"


@pytest.mark.parametrize("model_name", ["Team", "ConsentForm", "SourceMaterial"])
def test_export_detail_does_not_clobber_curated_serializer_of_same_model(v2_components, model_name):
    """The export '<Model>Detail' coexists with the curated public/inspect serializer that holds the
    bare model name -- the two are distinct components, not one colliding definition."""
    assert model_name in v2_components, f"curated '{model_name}' component went missing"
    assert f"{model_name}Detail" in v2_components
