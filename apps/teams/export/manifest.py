"""The single maintenance surface for the team data sync: which models to pull, in what order,
and the per-model config (secrets, team scoping, global matching) the endpoint and importer need."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.core.exceptions import FieldDoesNotExist
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import ForeignKey, Model, Prefetch, Q, QuerySet


@dataclass(frozen=True)
class ManifestEntry:
    model: str  # "app_label.model" — the internal key (matches Model._meta.label_lower)
    resource: str  # the URL-facing name the sync endpoint is mounted at
    cursor: str  # pk | updated_at_id
    secret: bool = False


# Pulled in dependency order: a referencing model always follows what it points at. Versioned models
# (source material, consent form, pipeline, node, chatbot) page by "pk": a working version is always
# created before its published copies, so it has a lower id and is served -- and imported -- first,
# letting the self-referential working_version FK resolve. Guarded by
# test_working_version_always_served_before_its_published_copies.
# The team itself is synced too, but not as a generic paginated resource. It's auto-resolved from
# the API key, served as a single object at the ``team/`` root (see apps/api/export/views.TeamView),
# and imported first as the anchor every other row is reassigned to -- so it's deliberately absent
# from MANIFEST_ENTRIES. ``test_every_first_party_model_is_synced_or_ignored`` accounts for it.
TEAM_MODEL = "teams.team"

MANIFEST_ENTRIES: list[ManifestEntry] = [
    ManifestEntry("users.customuser", "users", "pk"),
    ManifestEntry("service_providers.llmprovider", "llm_providers", "pk", secret=True),
    ManifestEntry("service_providers.voiceprovider", "voice_providers", "pk", secret=True),
    ManifestEntry("service_providers.messagingprovider", "messaging_providers", "pk", secret=True),
    ManifestEntry("service_providers.authprovider", "auth_providers", "pk", secret=True),
    ManifestEntry("service_providers.traceprovider", "trace_providers", "pk", secret=True),
    ManifestEntry("service_providers.llmprovidermodel", "llm_provider_models", "pk"),
    ManifestEntry("service_providers.embeddingprovidermodel", "embedding_provider_models", "pk"),
    # Team-specific pricing overrides only; global rules (team=NULL) are excluded by the team filter
    # and seeded on the target via load_ai_pricing. Before usage_records, which reference it.
    ManifestEntry("cost_tracking.pricingrule", "pricing_rules", "pk"),
    ManifestEntry("documents.collection", "collections", "pk"),
    ManifestEntry("files.file", "files", "pk"),
    ManifestEntry("experiments.syntheticvoice", "synthetic_voices", "pk"),
    ManifestEntry("custom_actions.customaction", "custom_actions", "pk"),
    ManifestEntry("experiments.sourcematerial", "source_materials", "pk"),
    ManifestEntry("experiments.consentform", "consent_forms", "pk"),
    ManifestEntry("annotations.tag", "tags", "pk"),
    ManifestEntry("documents.documentsource", "document_sources", "pk", secret=True),
    ManifestEntry("documents.collectionfile", "collection_files", "pk"),
    ManifestEntry("files.filechunkembedding", "file_chunk_embeddings", "pk"),
    ManifestEntry("ocs_notifications.eventtype", "notification_event_types", "pk"),
    ManifestEntry("ocs_notifications.usernotificationpreferences", "user_notification_preferences", "pk"),
    ManifestEntry("pipelines.pipeline", "pipelines", "pk"),
    ManifestEntry("pipelines.node", "pipeline_nodes", "pk"),
    ManifestEntry("custom_actions.customactionoperation", "custom_action_operations", "pk"),
    ManifestEntry("experiments.experiment", "chatbots", "pk"),
    ManifestEntry("bot_channels.experimentchannel", "chatbot_channels", "pk", secret=True),
    ManifestEntry("events.eventaction", "event_actions", "pk"),
    ManifestEntry("events.statictrigger", "static_triggers", "pk"),
    ManifestEntry("events.timeouttrigger", "timeout_triggers", "pk"),
    ManifestEntry("experiments.participant", "participants", "updated_at_id"),
    ManifestEntry("experiments.participantdata", "participant_data", "updated_at_id", secret=True),
    ManifestEntry("chat.chat", "chats", "updated_at_id"),
    ManifestEntry("experiments.experimentsession", "sessions", "updated_at_id"),
    ManifestEntry("chat.chatattachment", "chat_attachments", "pk"),
    ManifestEntry("chat.chatmessage", "chat_messages", "updated_at_id"),
    ManifestEntry("trace.trace", "traces", "pk"),
    ManifestEntry("pipelines.pipelinechathistory", "pipeline_chat_histories", "updated_at_id"),
    ManifestEntry("pipelines.pipelinechatmessages", "pipeline_chat_messages", "pk"),
    ManifestEntry("events.scheduledmessage", "scheduled_messages", "updated_at_id"),
    ManifestEntry("ocs_notifications.notificationevent", "notification_events", "pk"),
    ManifestEntry("ocs_notifications.eventuser", "event_users", "updated_at_id"),
    ManifestEntry("evaluations.evaluator", "evaluators", "updated_at_id"),
    ManifestEntry("evaluations.evaluationmessage", "evaluation_messages", "updated_at_id"),
    ManifestEntry("evaluations.evaluationdataset", "evaluation_datasets", "updated_at_id"),
    ManifestEntry("evaluations.datasetautopopulationrule", "dataset_auto_population_rules", "updated_at_id"),
    ManifestEntry("evaluations.evaluationconfig", "evaluation_configs", "updated_at_id"),
    ManifestEntry("evaluations.evaluatortagrule", "evaluator_tag_rules", "updated_at_id"),
    ManifestEntry("evaluations.evaluationrun", "evaluation_runs", "updated_at_id"),
    ManifestEntry("evaluations.evaluationresult", "evaluation_results", "pk"),
    ManifestEntry("evaluations.evaluationrunaggregate", "evaluation_run_aggregates", "updated_at_id"),
    ManifestEntry("evaluations.appliedtag", "applied_tags", "pk"),
    ManifestEntry("human_annotations.annotationqueue", "annotation_queues", "updated_at_id"),
    ManifestEntry("human_annotations.annotationitem", "annotation_items", "updated_at_id"),
    ManifestEntry("human_annotations.annotation", "annotations", "updated_at_id"),
    ManifestEntry("human_annotations.annotationqueueaggregate", "annotation_queue_aggregates", "updated_at_id"),
    ManifestEntry("analysis.transcriptanalysis", "transcript_analyses", "updated_at_id"),
    ManifestEntry("analysis.analysisquery", "analysis_queries", "pk"),
    ManifestEntry("cost_tracking.usagerecord", "usage_records", "pk"),
    # Generic-FK models last: their content_object can point at many models, so every possible target
    # is imported before them.
    ManifestEntry("annotations.customtaggeditem", "custom_tagged_items", "pk"),
    ManifestEntry("annotations.usercomment", "user_comments", "updated_at_id"),
    ManifestEntry("assessments.score", "scores", "updated_at_id"),
]

# Fields sealed under the team's public key in transit (encrypted-at-rest or sensitive-by-policy).
SECRET_REGISTRY: dict[str, list[str]] = {
    "service_providers.llmprovider": ["config"],
    "service_providers.voiceprovider": ["config"],
    "service_providers.messagingprovider": ["config"],
    "service_providers.authprovider": ["config"],
    "service_providers.traceprovider": ["config"],
    "experiments.participantdata": ["data", "encryption_key"],
    "documents.documentsource": ["config"],
    "bot_channels.experimentchannel": ["extra_data"],
}

# Fields dropped from the serialized row: re-established on the target (password) or deliberately
# not propagated (is_staff/is_superuser are crosscutting perms). The user's auth ``groups`` stay in --
# the serializer's ``groups`` method field overrides them with the team role.
EXCLUDE_REGISTRY: dict[str, list[str]] = {
    "teams.team": ["members", "public_key"],
    "users.customuser": ["password", "user_permissions", "is_staff", "is_superuser"],
    # Collection.files / DocumentSource.files are M2M through CollectionFile (extra columns). Excluding
    # them stops the serializer emitting a bare pk list the importer would .set() -- Django rejects
    # that for explicit through models. CollectionFile rows (their own entry) carry the link.
    "documents.collection": ["files"],
    "documents.documentsource": ["files"],
}

# ORM lookup path from a model to its owning team, applied as Model.objects.filter(<path>=team).
# Default is "team" (the direct FK on every BaseTeamModel); only models without one need an entry.
TEAM_PATH_REGISTRY: dict[str, str | list[str]] = {
    "users.customuser": "teams",
    "pipelines.node": "pipeline__team",
    "experiments.syntheticvoice": "voice_provider__team",
    "chat.chatmessage": "chat__team",
    "chat.chatattachment": "chat__team",
    "custom_actions.customactionoperation": "custom_action__team",
    "documents.collectionfile": "collection__team",
    "events.statictrigger": "experiment__team",
    "events.timeouttrigger": "experiment__team",
    # EventAction has no team FK; StaticTrigger and TimeoutTrigger each hold a OneToOneField to it.
    "events.eventaction": [
        "static_trigger__experiment__team",
        "timeout_trigger__experiment__team",
    ],
    "pipelines.pipelinechathistory": "session__team",
    "pipelines.pipelinechatmessages": "chat_history__session__team",
    "evaluations.evaluationrunaggregate": "run__team",
    "evaluations.evaluationmessage": [
        "session__team",
        "input_chat_message__chat__team",
        "evaluationdataset__team",
    ],
    "analysis.analysisquery": "analysis__team",
}

# Additional queryset filters applied after team scoping, for models that need row-level exclusions
# beyond the team boundary (e.g. rows that reference a deliberately-excluded model).
EXTRA_FILTERS: dict[str, Q] = {
    # Assistant-attached operations can't be synced (assistants are excluded). Only node-attached
    # operations, so the check constraint (assistant OR node non-null) is satisfied on import.
    "custom_actions.customactionoperation": Q(node__isnull=False),
}


def _customuser_prefetch(team) -> list:
    """Prefetch only the exported team's membership and its role groups, filtered in the DB. Keeps the
    user's role-group field from pulling in the user's other-team memberships (and from querying per
    row in the serializer)."""
    membership = apps.get_model("teams", "membership")
    return [Prefetch("membership_set", queryset=membership.objects.filter(team=team).prefetch_related("groups"))]


# Per-model prefetches, built per request because some are scoped to the team being synced.
PREFETCH_REGISTRY: dict[str, Callable[[object], list]] = {
    "users.customuser": _customuser_prefetch,
}


@dataclass(frozen=True)
class GlobalSpec:
    """A model whose global rows (the null-field set to null) are shared across servers: they are
    served alongside the team's rows and matched on the target by natural key rather than recreated."""

    null_field: str
    natural_key: tuple[str, ...]


GLOBAL_CONFIG: dict[str, GlobalSpec] = {
    "service_providers.llmprovidermodel": GlobalSpec("team", ("type", "name", "max_token_limit")),
    "service_providers.embeddingprovidermodel": GlobalSpec("team", ("type", "name")),
    "experiments.syntheticvoice": GlobalSpec(
        "voice_provider", ("name", "language_code", "language", "gender", "neural", "service")
    ),
}

_ENTRIES_BY_RESOURCE = {entry.resource: entry for entry in MANIFEST_ENTRIES}


def get_manifest_entry(resource: str) -> ManifestEntry | None:
    return _ENTRIES_BY_RESOURCE.get(resource)


def entry_model(model_label: str) -> type[Model]:
    return apps.get_model(*model_label.split("."))


def generic_fk_fields(model: type[Model]) -> list[tuple[str, str]]:
    """(content_type field, object_id field) name pairs for each GenericForeignKey on the model. Read
    off the GFK itself so the differing column names (``content_type``/``object_id`` vs
    ``target_content_type``/``target_object_id``) need no hardcoding -- the serializer and importer
    both drive their generic-FK handling from this."""
    return [(f.ct_field, f.fk_field) for f in model._meta.private_fields if isinstance(f, GenericForeignKey)]


def model_has_team_field(model: type[Model]) -> bool:
    """Whether the model has a direct ``team`` FK -- the per-row team id that's dropped from the
    export and re-assigned to the synced team on import."""
    try:
        return isinstance(model._meta.get_field("team"), ForeignKey)
    except FieldDoesNotExist:
        return False


def team_scoped_queryset(entry: ManifestEntry, team) -> QuerySet:
    """The model's rows for this team, including any shared global rows."""
    model = entry_model(entry.model)
    paths = TEAM_PATH_REGISTRY.get(entry.model, "team")
    if isinstance(paths, str):
        paths = [paths]
    team_q = Q()
    for p in paths:
        team_q |= Q(**{p: team.pk if p in ("pk", "id") else team})
    spec = GLOBAL_CONFIG.get(entry.model)
    if spec:
        team_q |= Q(**{f"{spec.null_field}__isnull": True})
    queryset = model.objects.filter(team_q)
    if len(paths) > 1:
        queryset = queryset.distinct()
    extra_filter = EXTRA_FILTERS.get(entry.model)
    if extra_filter is not None:
        queryset = queryset.filter(extra_filter)

    prefetch_factory = PREFETCH_REGISTRY.get(entry.model)
    return queryset.prefetch_related(*prefetch_factory(team)) if prefetch_factory else queryset


def schema_checksum() -> str:
    """Hash of the set of applied migrations, identified by ``(app, name)`` -- the same key
    ``MigrationRecorder`` uses, so schemas that share a filename like ``0001_initial`` across apps
    don't collide. Unaffected by apply order. Returns the hash of an empty set when the migrations
    table is absent (e.g. tests run with --no-migrations); that's consistent as long as the source
    and target both lack it."""
    recorder = MigrationRecorder(connection)
    applied = []
    if recorder.has_table():
        applied = list(recorder.migration_qs.order_by("app", "name").values_list("app", "name"))
    data = json.dumps(applied, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def build_manifest() -> dict:
    return {
        "schema_checksum": schema_checksum(),
        "entries": [
            {
                "model": e.model,
                "resource": e.resource,
                "cursor": e.cursor,
                "secret": e.secret,
            }
            for e in MANIFEST_ENTRIES
        ],
    }
