import pytest
from django.apps import apps

from apps.teams.export import manifest
from apps.utils.factories.service_provider_factories import LlmProviderModelFactory
from apps.utils.factories.team import TeamFactory


def _model(label):
    return apps.get_model(*label.split("."))


# Test-only partition data (the sync code never reads these). First-party models deliberately left out
# of the sync; the partition test asserts every model is in MANIFEST_ENTRIES, here, or embedded, so a
# newly added model forces a sync/ignore decision.
IGNORED_MODELS = frozenset(
    {
        "analysis.analysisquery",
        "analysis.transcriptanalysis",
        "annotations.customtaggeditem",
        "annotations.tag",
        "annotations.usercomment",
        "api.userapikey",
        "assessments.score",
        "assistants.openaiassistant",
        "assistants.toolresources",
        "banners.banner",
        "bot_channels.experimentchannel",
        "chat.chatattachment",
        "chat.chatmessage",
        "cost_tracking.pricingrule",
        "cost_tracking.usagerecord",
        "custom_actions.customaction",
        "custom_actions.customactionoperation",
        "dashboard.dashboardcache",
        "dashboard.dashboardfilter",
        "data_migrations.custommigration",
        "documents.collection",
        "documents.collectionfile",
        "documents.documentsource",
        "documents.documentsourcesynclog",
        "evaluations.appliedtag",
        "evaluations.datasetautopopulationrule",
        "evaluations.evaluationconfig",
        "evaluations.evaluationdataset",
        "evaluations.evaluationmessage",
        "evaluations.evaluationresult",
        "evaluations.evaluationrun",
        "evaluations.evaluationrunaggregate",
        "evaluations.evaluator",
        "evaluations.evaluatortagrule",
        "events.eventaction",
        "events.eventlog",
        "events.scheduledmessage",
        "events.scheduledmessageattempt",
        "events.statictrigger",
        "events.timeouttrigger",
        "experiments.promptbuilderhistory",
        "experiments.survey",
        "files.file",
        "files.filechunkembedding",
        "filters.filterset",
        "human_annotations.annotation",
        "human_annotations.annotationitem",
        "human_annotations.annotationqueue",
        "human_annotations.annotationqueueaggregate",
        "mcp_integrations.mcpserver",
        "oauth.oauth2accesstoken",
        "oauth.oauth2application",
        "oauth.oauth2grant",
        "oauth.oauth2idtoken",
        "oauth.oauth2refreshtoken",
        "ocs_notifications.eventtype",
        "ocs_notifications.eventuser",
        "ocs_notifications.notificationevent",
        "ocs_notifications.usernotificationpreferences",
        "pipelines.pipelinechathistory",
        "pipelines.pipelinechatmessages",
        "site_admin.ocsconfiguration",
        "slack.slackbot",
        "slack.slackinstallation",
        "slack.slackoauthstate",
        "sso.ssosession",
        "teams.flag",
        "teams.invitation",
        "trace.trace",
    }
)

# Synced inside another resource's row, not as a standalone resource: a membership rides in its
# user's row (the team role).
EMBEDDED_MODELS = frozenset({"teams.membership"})


def test_every_entry_resolves_to_a_model():
    for entry in manifest.MANIFEST_ENTRIES:
        assert _model(entry.model) is not None


def test_entry_cursor_values_are_valid():
    for entry in manifest.MANIFEST_ENTRIES:
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
    first_party = {
        m._meta.label_lower
        for m in apps.get_models()
        # Real first-party apps only; test-scaffolding apps register as ``apps.<x>.tests`` and their
        # models leak in here under xdist depending on which tests share the worker.
        if m._meta.app_config.name.startswith("apps.") and "tests" not in m._meta.app_config.name.split(".")
    }
    # The team is synced via a dedicated step (TEAM_MODEL); embedded models (membership) ride
    # inside another resource's rows rather than being served as generic manifest resources.
    classified = {e.model for e in manifest.MANIFEST_ENTRIES} | IGNORED_MODELS | EMBEDDED_MODELS | {manifest.TEAM_MODEL}
    unclassified = first_party - classified
    assert not unclassified, "Add these to MANIFEST_ENTRIES or IGNORED_MODELS: " + ", ".join(sorted(unclassified))


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

    entry = manifest.get_manifest_entry("llm_provider_models")
    pks = set(manifest.team_scoped_queryset(entry, team).values_list("pk", flat=True))
    assert mine.pk in pks
    assert global_model.pk in pks
    assert theirs.pk not in pks


@pytest.mark.django_db()
def test_build_manifest_payload_shape():
    payload = manifest.build_manifest()
    assert isinstance(payload["schema_checksum"], str)
    assert {e["resource"] for e in payload["entries"]} == {e.resource for e in manifest.MANIFEST_ENTRIES}
    first = payload["entries"][0]
    assert set(first) >= {"model", "resource", "cursor", "secret"}
