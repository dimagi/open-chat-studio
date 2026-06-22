"""The single maintenance surface for the team data sync: which models to pull, in what order,
and the per-model config (secrets, team scoping, global matching) the endpoint and importer need."""

from dataclasses import dataclass

from django.apps import apps
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Model, Q, QuerySet


@dataclass(frozen=True)
class ManifestEntry:
    model: str  # "app_label.model" — the internal key (matches Model._meta.label_lower)
    resource: str  # the URL-facing name the sync endpoint is mounted at
    phase: str  # structural | live | structural+live
    cursor: str  # pk | updated_at_id
    secret: bool = False
    order_by: str | None = None


_WORKING_FIRST = "working_version_id_nulls_first"

# Pulled in dependency order: a referencing model always follows what it points at.
MANIFEST_ENTRIES: list[ManifestEntry] = [
    ManifestEntry("teams.team", "teams", "structural", "pk"),
    ManifestEntry("users.customuser", "user", "structural", "pk"),
    ManifestEntry("teams.membership", "membership", "structural", "pk"),
    ManifestEntry("service_providers.llmprovider", "llm_provider", "structural", "pk", secret=True),
    ManifestEntry("service_providers.voiceprovider", "voice_provider", "structural", "pk", secret=True),
    ManifestEntry("service_providers.messagingprovider", "messaging_provider", "structural", "pk", secret=True),
    ManifestEntry("service_providers.authprovider", "auth_provider", "structural", "pk", secret=True),
    ManifestEntry("service_providers.traceprovider", "trace_provider", "structural", "pk", secret=True),
    ManifestEntry("service_providers.llmprovidermodel", "llm_provider_model", "structural", "pk"),
    ManifestEntry("service_providers.embeddingprovidermodel", "embedding_provider_model", "structural", "pk"),
    ManifestEntry("experiments.syntheticvoice", "synthetic_voice", "structural", "pk"),
    ManifestEntry("experiments.sourcematerial", "source_material", "structural", "pk", order_by=_WORKING_FIRST),
    ManifestEntry("experiments.consentform", "consent_form", "structural", "pk", order_by=_WORKING_FIRST),
    ManifestEntry("pipelines.pipeline", "pipeline", "structural", "pk", order_by=_WORKING_FIRST),
    ManifestEntry("pipelines.node", "node", "structural", "pk", order_by=_WORKING_FIRST),
    ManifestEntry("experiments.experiment", "chatbot", "structural", "pk", order_by=_WORKING_FIRST),
    ManifestEntry("experiments.participant", "participant", "live", "updated_at_id"),
    ManifestEntry("experiments.participantdata", "participant_data", "live", "updated_at_id", secret=True),
    ManifestEntry("chat.chat", "chat", "live", "pk"),
    ManifestEntry("experiments.experimentsession", "experiment_session", "live", "updated_at_id"),
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

# Fields dropped from the serialized row. Re-established on the target rather than copied.
EXCLUDE_REGISTRY: dict[str, list[str]] = {
    "teams.team": ["members", "public_key"],
    "users.customuser": ["password", "groups", "user_permissions"],
}

# ORM lookup path from a model to its owning team, applied as Model.objects.filter(<path>=team).
# Default is "team" (the direct FK on every BaseTeamModel); only models without one need an entry.
TEAM_PATH_REGISTRY: dict[str, str] = {
    "teams.team": "pk",
    "users.customuser": "teams",
    "pipelines.node": "pipeline__team",
    "experiments.syntheticvoice": "voice_provider__team",
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


def get_entry(resource: str) -> ManifestEntry | None:
    return _ENTRIES_BY_RESOURCE.get(resource)


def entry_model(model_label: str) -> type[Model]:
    return apps.get_model(*model_label.split("."))


def team_scoped_queryset(entry: ManifestEntry, team) -> QuerySet:
    """The model's rows for this team, including any shared global rows."""
    model = entry_model(entry.model)
    path = TEAM_PATH_REGISTRY.get(entry.model, "team")
    team_q = Q(**{path: team.pk if path in ("pk", "id") else team})
    spec = GLOBAL_CONFIG.get(entry.model)
    if spec:
        team_q |= Q(**{f"{spec.null_field}__isnull": True})
    return model.objects.filter(team_q)


def schema_checksum() -> int:
    """Hash of the set of applied migration names; unaffected by apply order."""
    from django.db import connection  # noqa: PLC0415

    names = MigrationRecorder(connection).migration_qs.order_by("name").values_list("name", flat=True)
    return hash(frozenset(names))


def build_manifest() -> dict:
    return {
        "schema_checksum": schema_checksum(),
        "entries": [
            {
                "model": e.model,
                "resource": e.resource,
                "phase": e.phase,
                "cursor": e.cursor,
                "secret": e.secret,
                **({"order_by": e.order_by} if e.order_by else {}),
            }
            for e in MANIFEST_ENTRIES
        ],
    }
