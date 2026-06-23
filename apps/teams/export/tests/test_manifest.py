import pytest
from django.apps import apps

from apps.teams.export import manifest
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory
from apps.utils.factories.team import TeamFactory


def _model(label):
    return apps.get_model(*label.split("."))


def test_every_entry_resolves_to_a_model():
    for entry in manifest.MANIFEST_ENTRIES:
        assert _model(entry.model) is not None


def test_entry_cursor_and_phase_values_are_valid():
    for entry in manifest.MANIFEST_ENTRIES:
        assert entry.cursor in {"pk", "updated_at_id"}
        assert entry.phase in {"structural", "live", "structural+live"}


def test_models_and_resources_are_unique():
    models = [e.model for e in manifest.MANIFEST_ENTRIES]
    resources = [e.resource for e in manifest.MANIFEST_ENTRIES]
    assert len(models) == len(set(models))
    assert len(resources) == len(set(resources))


def test_secret_flag_agrees_with_secret_registry():
    for entry in manifest.MANIFEST_ENTRIES:
        assert entry.secret == (entry.model in manifest.SECRET_REGISTRY)


def test_registry_keys_resolve_to_models():
    for label in (
        *manifest.SECRET_REGISTRY,
        *manifest.EXCLUDE_REGISTRY,
        *manifest.TEAM_PATH_REGISTRY,
        *manifest.IGNORED_MODELS,
    ):
        assert _model(label) is not None


def test_every_first_party_model_is_synced_or_ignored():
    """Every app model is either in the manifest or explicitly ignored, so a newly added model
    can't slip past the sync unnoticed -- it forces a choice between syncing and ignoring it."""
    first_party = {
        m._meta.label_lower
        for m in apps.get_models()
        # Real first-party apps only; test-scaffolding apps register as ``apps.<x>.tests`` and their
        # models leak in here under xdist depending on which tests share the worker.
        if m._meta.app_config.name.startswith("apps.") and "tests" not in m._meta.app_config.name.split(".")
    }
    classified = {e.model for e in manifest.MANIFEST_ENTRIES} | manifest.IGNORED_MODELS
    unclassified = first_party - classified
    assert not unclassified, "Add these to MANIFEST_ENTRIES or IGNORED_MODELS: " + ", ".join(sorted(unclassified))


def test_manifest_and_ignored_models_are_disjoint():
    in_manifest = {e.model for e in manifest.MANIFEST_ENTRIES}
    assert not (in_manifest & manifest.IGNORED_MODELS)


def test_registry_fields_exist_on_their_model():
    for label, fields in (*manifest.SECRET_REGISTRY.items(), *manifest.EXCLUDE_REGISTRY.items()):
        model_fields = {f.name for f in _model(label)._meta.get_fields()}
        for field in fields:
            assert field in model_fields, f"{label}.{field}"


def test_get_manifest_entry_returns_matching_entry():
    entry = manifest.get_manifest_entry("teams")
    assert entry.resource == "teams"
    assert entry.model == "teams.team"
    assert manifest.get_manifest_entry("not_a_resource") is None


def test_versioned_entries_order_by_working_version_first():
    for resource in ("pipeline", "chatbot"):
        assert manifest.get_manifest_entry(resource).order_by == "working_version_id_nulls_first"


@pytest.mark.django_db()
def test_schema_checksum_is_reproducible():
    first = manifest.schema_checksum()
    assert isinstance(first, str)
    assert first == manifest.schema_checksum()


@pytest.mark.django_db()
def test_team_scoped_queryset_isolates_teams_and_includes_globals():
    team = TeamFactory()
    other = TeamFactory()
    mine = LlmProviderModelFactory(team=team)
    theirs = LlmProviderModelFactory(team=other)
    global_model = LlmProviderModelFactory(team=None)

    entry = manifest.get_manifest_entry("llm_provider_model")
    pks = set(manifest.team_scoped_queryset(entry, team).values_list("pk", flat=True))
    assert mine.pk in pks
    assert global_model.pk in pks
    assert theirs.pk not in pks


@pytest.mark.django_db()
def test_team_scoped_queryset_scopes_team_to_itself():
    team = TeamFactory()
    other = TeamFactory()
    pks = set(manifest.team_scoped_queryset(manifest.get_manifest_entry("teams"), team).values_list("pk", flat=True))
    assert pks == {team.pk}
    assert other.pk not in pks


@pytest.mark.django_db()
def test_build_manifest_payload_shape():
    payload = manifest.build_manifest()
    assert isinstance(payload["schema_checksum"], str)
    assert {e["resource"] for e in payload["entries"]} == {e.resource for e in manifest.MANIFEST_ENTRIES}
    first = payload["entries"][0]
    assert set(first) >= {"model", "resource", "phase", "cursor", "secret"}
