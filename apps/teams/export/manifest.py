"""The single maintenance surface for the team data sync: which models to pull, in what order,
and the per-model config (secrets, team scoping, global matching) the endpoint and importer need."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from django.apps import apps
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
    ManifestEntry("experiments.syntheticvoice", "synthetic_voices", "pk"),
    ManifestEntry("experiments.sourcematerial", "source_materials", "pk"),
    ManifestEntry("experiments.consentform", "consent_forms", "pk"),
    ManifestEntry("pipelines.pipeline", "pipelines", "pk"),
    ManifestEntry("pipelines.node", "pipeline_nodes", "pk"),
    ManifestEntry("experiments.experiment", "chatbots", "pk"),
    ManifestEntry("experiments.participant", "participants", "updated_at_id"),
    ManifestEntry("experiments.participantdata", "participant_data", "updated_at_id", secret=True),
    ManifestEntry("chat.chat", "chats", "updated_at_id"),
    ManifestEntry("experiments.experimentsession", "sessions", "updated_at_id"),
]

# Fields sealed under the team's public key in transit (encrypted-at-rest or sensitive-by-policy).
SECRET_REGISTRY: dict[str, list[str]] = {
    "service_providers.llmprovider": ["config"],
    "service_providers.voiceprovider": ["config"],
    "service_providers.messagingprovider": ["config"],
    "service_providers.authprovider": ["config"],
    "service_providers.traceprovider": ["config"],
    "experiments.participantdata": ["data", "encryption_key"],
}

# Fields dropped from the serialized row: re-established on the target (password) or deliberately
# not propagated (is_staff/is_superuser are crosscutting perms). The user's auth ``groups`` stay in --
# the serializer's ``groups`` method field overrides them with the team role.
EXCLUDE_REGISTRY: dict[str, list[str]] = {
    "teams.team": ["members", "public_key"],
    "users.customuser": ["password", "user_permissions", "is_staff", "is_superuser"],
}

# ORM lookup path from a model to its owning team, applied as Model.objects.filter(<path>=team).
# Default is "team" (the direct FK on every BaseTeamModel); only models without one need an entry.
TEAM_PATH_REGISTRY: dict[str, str] = {
    "users.customuser": "teams",
    "pipelines.node": "pipeline__team",
    "experiments.syntheticvoice": "voice_provider__team",
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
    path = TEAM_PATH_REGISTRY.get(entry.model, "team")
    team_q = Q(**{path: team.pk if path in ("pk", "id") else team})
    spec = GLOBAL_CONFIG.get(entry.model)
    if spec:
        team_q |= Q(**{f"{spec.null_field}__isnull": True})
    queryset = model.objects.filter(team_q)
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
