"""Tests for the per-resource sync endpoint documentation: each synced resource is mounted at its
own literal path (so OpenAPI can give it a distinct response schema) and routed to a ResourceView
subclass carrying that schema."""

import pytest
from django.urls import resolve
from rest_framework import serializers

from apps.api.general.schema import (
    build_docs_item_serializer,
    build_resource_response_serializer,
    resource_responses,
)
from apps.api.general.serializers import ManifestEntrySerializer, ManifestSerializer
from apps.api.general.views import ResourceView
from apps.teams.export.manifest import MANIFEST_ENTRIES, build_manifest, get_manifest_entry


def test_each_resource_resolves_to_a_resourceview_carrying_its_name():
    for entry in MANIFEST_ENTRIES:
        match = resolve(f"/api/v2/resources/{entry.resource}/")
        assert issubclass(match.func.cls, ResourceView)
        assert match.kwargs == {"resource": entry.resource}


def test_unknown_resource_resolves_to_catchall_view():
    # A catch-all route lets the view return a JSON 404 rather than Django's HTML 404.
    match = resolve("/api/v2/resources/not_a_real_resource/")
    assert issubclass(match.func.cls, ResourceView)
    assert match.kwargs == {"resource": "not_a_real_resource"}


def test_response_envelope_has_cursor_has_more_and_results():
    fields = build_resource_response_serializer(get_manifest_entry("teams"))().fields
    assert set(fields) == {"cursor", "has_more", "results"}
    assert isinstance(fields["results"], serializers.ListSerializer)


@pytest.mark.parametrize(
    ("resource", "expect_409"),
    [
        pytest.param("llm_providers", True, id="secret-resource-documents-409"),
        pytest.param("teams", False, id="plain-resource-has-no-409"),
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
    fields = build_docs_item_serializer(get_manifest_entry("llm_providers"))().fields
    assert isinstance(fields["config"], serializers.CharField)


@pytest.mark.django_db()
def test_manifest_serializer_matches_build_manifest_payload():
    """The documented manifest response must stay in step with what the view actually returns."""
    manifest = build_manifest()
    assert set(ManifestSerializer().fields) == set(manifest)
    declared = set(ManifestEntrySerializer().fields)
    actual_keys = set().union(*(entry.keys() for entry in manifest["entries"]))
    assert declared == actual_keys
