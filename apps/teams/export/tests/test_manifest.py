import pytest
from django.apps import apps

from apps.annotations.models import TaggedModelMixin, UserCommentsMixin
from apps.api.export.serializers import build_resource_serializer
from apps.files.models import FilePurpose
from apps.teams.export import manifest
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.cost_tracking import PricingRuleFactory
from apps.utils.factories.documents import CollectionFactory, CollectionFileFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.files import FileFactory
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory
from apps.utils.factories.team import TeamFactory


def _model(label):
    return apps.get_model(*label.split("."))


def _first_party_models():
    """First-party app models only. Test-scaffolding apps register as ``apps.<x>.tests`` and their
    models leak into ``apps.get_models()`` under xdist depending on which tests share a worker."""
    return [
        m
        for m in apps.get_models()
        if m._meta.app_config.name.startswith("apps.") and "tests" not in m._meta.app_config.name.split(".")
    ]


# Test-only partition data (the sync code never reads these). First-party models deliberately left out
# of the sync; the partition test asserts every model is in MANIFEST_ENTRIES, here, or embedded, so a
# newly added model forces a sync/ignore decision.
IGNORED_MODELS = frozenset(
    {
        "api.userapikey",
        "assistants.openaiassistant",
        "assistants.toolresources",
        "banners.banner",
        "dashboard.dashboardcache",
        "dashboard.dashboardfilter",
        "data_migrations.custommigration",
        "documents.documentsourcesynclog",
        "events.eventlog",
        "events.scheduledmessageattempt",
        "experiments.promptbuilderhistory",
        "filters.filterset",
        "mcp_integrations.mcpserver",
        "oauth.oauth2accesstoken",
        "oauth.oauth2application",
        "oauth.oauth2grant",
        "oauth.oauth2idtoken",
        "oauth.oauth2refreshtoken",
        "site_admin.ocsconfiguration",
        "slack.slackbot",
        "slack.slackinstallation",
        "slack.slackoauthstate",
        "sso.ssosession",
        "teams.flag",
        "teams.invitation",
    }
)

# Synced inside another resource's row, not as a standalone resource: a membership rides in its
# user's row (the team role).
EMBEDDED_MODELS = frozenset({"teams.membership"})


def test_entries_resolve_to_models_with_valid_cursors():
    for entry in manifest.MANIFEST_ENTRIES:
        assert _model(entry.model) is not None
        assert entry.cursor in {"pk", "updated_at_id"}


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
        *IGNORED_MODELS,
    ):
        assert _model(label) is not None


def test_every_first_party_model_is_synced_or_ignored():
    """Every app model is either in the manifest or explicitly ignored, so a newly added model
    can't slip past the sync unnoticed -- it forces a choice between syncing and ignoring it."""
    first_party = {m._meta.label_lower for m in _first_party_models()}
    # The team is synced via a dedicated step (TEAM_MODEL); embedded models (membership) ride
    # inside another resource's rows rather than being served as generic manifest resources.
    classified = {e.model for e in manifest.MANIFEST_ENTRIES} | IGNORED_MODELS | EMBEDDED_MODELS | {manifest.TEAM_MODEL}
    unclassified = first_party - classified
    assert not unclassified, "Add these to MANIFEST_ENTRIES or IGNORED_MODELS: " + ", ".join(sorted(unclassified))


def test_generic_fk_target_models_are_synced():
    """Every model a synced generic-FK row (a tag, comment, or score) can point at must itself be in
    the manifest. The importer translates a generic FK straight through the store with no runtime
    guard, so an unsynced target would write a null object_id into a non-null column and the row would
    fail to import -- this pins the invariant at CI time instead."""
    # Taggable and commentable models declare those relations via mixins; the session is the only
    # Score target (see assessments.score_writers, v1).
    target_models = {"experiments.experimentsession"}
    for model in _first_party_models():
        if issubclass(model, (TaggedModelMixin, UserCommentsMixin)):
            target_models.add(model._meta.label_lower)

    synced = {entry.model for entry in manifest.MANIFEST_ENTRIES} | {manifest.TEAM_MODEL}
    missing = target_models - synced
    assert not missing, "Generic-FK target models must be in MANIFEST_ENTRIES: " + ", ".join(sorted(missing))


def test_manifest_ignored_and_embedded_models_are_disjoint():
    in_manifest = {e.model for e in manifest.MANIFEST_ENTRIES}
    assert not (in_manifest & IGNORED_MODELS)
    assert not (in_manifest & EMBEDDED_MODELS)
    assert not (IGNORED_MODELS & EMBEDDED_MODELS)


def test_embedded_models_resolve_to_models():
    for label in EMBEDDED_MODELS:
        assert _model(label) is not None


def test_membership_is_embedded_not_a_standalone_resource():
    """Membership rides inside the user export (the team role), not served as its own resource."""
    assert "teams.membership" in EMBEDDED_MODELS
    assert manifest.get_manifest_entry("memberships") is None


def test_registry_fields_exist_on_their_model():
    for label, fields in (*manifest.SECRET_REGISTRY.items(), *manifest.EXCLUDE_REGISTRY.items()):
        model_fields = {f.name for f in _model(label)._meta.get_fields()}
        for field in fields:
            assert field in model_fields, f"{label}.{field}"


def test_get_manifest_entry_returns_matching_entry():
    entry = manifest.get_manifest_entry("users")
    assert entry.resource == "users"
    assert entry.model == "users.customuser"
    # The team is not a generic resource -- it's served on its own at the ``team/`` root.
    assert manifest.get_manifest_entry("teams") is None
    assert manifest.get_manifest_entry("not_a_resource") is None


def test_schema_checksum_is_reproducible():
    first = manifest.schema_checksum()
    assert isinstance(first, str)
    manifest.schema_checksum.cache_clear()
    assert first == manifest.schema_checksum()


@pytest.mark.django_db()
def test_team_scoped_queryset_isolates_teams_and_includes_globals():
    """team_scoped_queryset returns the team's own rows plus shared global rows, never another team's."""
    team = TeamFactory()
    other = TeamFactory()
    mine = LlmProviderModelFactory(team=team)
    theirs = LlmProviderModelFactory(team=other)
    global_model = LlmProviderModelFactory(team=None)

    entry = manifest.get_manifest_entry("llm_provider_models")
    pks = set(manifest.team_scoped_queryset(entry, team).values_list("pk", flat=True))
    assert mine.pk in pks
    assert global_model.pk in pks
    assert theirs.pk not in pks


@pytest.mark.django_db()
def test_pricing_rules_queryset_excludes_global_rules():
    """Global pricing rules (team=NULL) are seeded on the target via load_ai_pricing, not synced, so
    the team-scoped queryset must return only the team's own override rules. pricing_rules is left out
    of GLOBAL_CONFIG deliberately -- this pins that decision."""
    team = TeamFactory()
    mine = PricingRuleFactory(team=team)
    global_rule = PricingRuleFactory(team=None)

    entry = manifest.get_manifest_entry("pricing_rules")
    pks = set(manifest.team_scoped_queryset(entry, team).values_list("pk", flat=True))
    assert mine.pk in pks
    assert global_rule.pk not in pks


@pytest.mark.django_db()
def test_files_queryset_excludes_data_export_files():
    """Data-export files are transient download bundles (24h expiry), not shareable content, so the
    team-scoped queryset must leave them out while keeping every other purpose."""
    team = TeamFactory()
    kept = FileFactory(team=team, purpose=FilePurpose.COLLECTION)
    export_file = FileFactory(team=team, purpose=FilePurpose.DATA_EXPORT)

    entry = manifest.get_manifest_entry("files")
    pks = set(manifest.team_scoped_queryset(entry, team).values_list("pk", flat=True))
    assert kept.pk in pks
    assert export_file.pk not in pks


@pytest.mark.django_db()
def test_collection_files_queryset_includes_files_of_archived_collections():
    """Archived collections are exported (team_scoped_queryset uses _base_manager), so their CollectionFile
    rows must be exported too -- otherwise the archived collection would arrive on the target with no
    files. The queryset must return files of both live and archived collections."""
    team = TeamFactory()
    # llm_provider/embedding_provider_model share a per-team unique key, so leave them unset to keep
    # two collections in the same team from colliding -- this test only cares about is_archived.
    live_collection = CollectionFactory(team=team, llm_provider=None, embedding_provider_model=None)
    archived_collection = CollectionFactory(
        team=team, llm_provider=None, embedding_provider_model=None, is_archived=True
    )
    live = CollectionFileFactory(collection=live_collection)
    of_archived = CollectionFileFactory(collection=archived_collection)

    entry = manifest.get_manifest_entry("collection_files")
    pks = set(manifest.team_scoped_queryset(entry, team).values_list("pk", flat=True))
    assert live.pk in pks
    assert of_archived.pk in pks


@pytest.mark.django_db()
def test_queryset_includes_archived_rows_of_versioned_models():
    """Versioned models filter is_archived=False on their default manager. The export must bypass that
    (via _base_manager) so archived rows are shared across servers along with the live ones."""
    team = TeamFactory()
    live = ExperimentFactory(team=team)
    archived = ExperimentFactory(team=team, is_archived=True)

    entry = manifest.get_manifest_entry("chatbots")
    pks = set(manifest.team_scoped_queryset(entry, team).values_list("pk", flat=True))
    assert live.pk in pks
    assert archived.pk in pks


@pytest.mark.django_db()
def test_queryset_includes_soft_deleted_channels():
    """ExperimentChannel's default manager filters deleted=False. The export bypasses that (via
    _base_manager) so soft-deleted channels are shared across servers too, keeping the snapshot
    faithful."""
    team = TeamFactory()
    live = ExperimentChannelFactory(team=team)
    deleted = ExperimentChannelFactory(team=team, deleted=True)

    entry = manifest.get_manifest_entry("chatbot_channels")
    pks = set(manifest.team_scoped_queryset(entry, team).values_list("pk", flat=True))
    assert live.pk in pks
    assert deleted.pk in pks


@pytest.mark.django_db()
def test_build_manifest_payload_shape():
    payload = manifest.build_manifest()
    assert isinstance(payload["schema_checksum"], str)
    assert {e["resource"] for e in payload["entries"]} == {e.resource for e in manifest.MANIFEST_ENTRIES}
    first = payload["entries"][0]
    assert set(first) >= {"model", "resource", "cursor", "secret"}


def test_every_resource_serializer_builds():
    """Building every entry's serializer forces DRF field mapping, so an unmapped field type (file,
    vector, pydantic) on any synced model fails here rather than at schema generation or request time."""

    for entry in manifest.MANIFEST_ENTRIES:
        serializer = build_resource_serializer(manifest.entry_model(entry.model))()
        assert serializer.fields  # accessing .fields builds them; raises on an unmapped field type
