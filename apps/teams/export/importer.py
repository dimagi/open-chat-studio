"""The import engine: pull a serialized row, rewrite every reference from source ids to target ids,
create or update the row locally, restore its timestamps, and record the FK translation. Kept as
plain functions plus a thin loop so the transforms can be unit-tested without a database."""

import copy
import functools
from collections.abc import Iterable

from django.contrib.auth.models import Group
from django.db import models, transaction
from django.utils.dateparse import parse_datetime
from field_audit.models import AuditAction, AuditingQuerySet

from apps.teams.models import Flag
from apps.teams.utils import set_current_team
from apps.utils.fields import as_int

from .manifest import GLOBAL_CONFIG, SECRET_REGISTRY, GlobalSpec, entry_model, model_has_team_field
from .manifest import MANIFEST_ENTRIES as _ENTRIES
from .seal import unseal
from .translation import FKTranslationStore

MANIFEST_LABELS = {entry.model for entry in _ENTRIES}


class UnresolvedForeignKey(Exception):
    """A required FK points at a row that should have been synced but wasn't (an ordering bug)."""


class MissingGlobalRow(Exception):
    """The source serves a shared/global row (e.g. an LLM provider model) that doesn't exist on the
    target. Globals are matched by natural key, never created, so the operator must add it to the
    target first rather than have references silently nulled or the sync abort deep in FK resolution."""

    def __init__(self, model_label: str, spec: "GlobalSpec", row: dict):
        natural_key = {key: row.get(key) for key in spec.natural_key}
        super().__init__(
            f"{model_label} global row {natural_key} is not present on the target. "
            f"Create it on the target server, then rerun the sync."
        )


def resolve_fk(field: models.ForeignKey, source_pk: int | None, store: FKTranslationStore) -> int | None:
    if source_pk is None:
        return None
    target_label = field.related_model._meta.label_lower
    if target_label not in MANIFEST_LABELS:
        return None  # deliberately not synced (e.g. assistant, collection); left null by design
    target = store.get_target(target_label, source_pk)
    if target is not None:
        return target
    if field.null:
        return None
    raise UnresolvedForeignKey(f"{field.model._meta.label_lower}.{field.name} -> {target_label}:{source_pk}")


@functools.cache
def _node_param_models() -> dict[str, str]:
    """param key (e.g. ``llm_provider_id``) -> the model label its id points at."""
    from apps.pipelines.models import Node  # noqa: PLC0415 - models load lazily

    mapping = {}
    for name in Node.resource_fk_fields():
        mapping[f"{name}_id"] = Node._meta.get_field(name).related_model._meta.label_lower
    indexes = Node._meta.get_field("collection_indexes")
    mapping["collection_index_ids"] = indexes.related_model._meta.label_lower
    return mapping


def remap_node_params(params: dict, store: FKTranslationStore) -> dict:
    """Rewrite the resource ids hidden inside a node's params. References to models the sync
    doesn't copy have no translation and are left as-is."""
    result = dict(params)
    for key, label in _node_param_models().items():
        if key not in result or result[key] in (None, "", 0):
            continue
        value = result[key]
        if isinstance(value, list):
            result[key] = [store.get_target(label, as_int(v)) or v for v in value]
        else:
            result[key] = store.get_target(label, as_int(value)) or value
    return result


def remap_pipeline_data(data: dict | None, store: FKTranslationStore) -> dict | None:
    if not data or "nodes" not in data:
        return data
    result = copy.deepcopy(data)
    for node in result["nodes"]:
        params = node.get("data", {}).get("params")
        if isinstance(params, dict):
            node["data"]["params"] = remap_node_params(params, store)
    return result


def unseal_secrets(row: dict, secret_fields: list[str], private_key) -> dict:
    result = dict(row)
    for field in secret_fields:
        if field in result and result[field] is not None:
            result[field] = unseal(result[field], private_key)
    return result


# Fields rendered as names (not a field dump); re-linked after create by matching on the target.
_NAMED_LINK_FIELDS = {
    "teams.team": ["feature_flags"],
    "teams.membership": ["groups"],
}


def _match_existing_user(model, row: dict, store: FKTranslationStore, target_team) -> models.Model | None:
    """Map onto a user that already exists on the target rather than colliding on the unique username."""
    return model.objects.filter(username=row["username"]).first()


def _match_default_consent_form(model, row: dict, store: FKTranslationStore, target_team) -> models.Model | None:
    """The target team's default consent form is auto-created on team creation; map the source's
    default onto it instead of creating a second one (which the per-team unique constraint forbids)."""
    if not row.get("is_default") or target_team is None:
        return None
    return model.objects.filter(team=target_team, is_default=True).first()


# Rows matched to a pre-existing target row by natural key instead of created.
_MATCH_EXISTING = {
    "users.customuser": _match_existing_user,
    "experiments.consentform": _match_default_consent_form,
}

# Matched/existing rows that must not be overwritten by synced values (an account the operator
# already has on the target keeps its own profile and permissions).
_NO_UPDATE_MODELS = {"users.customuser"}


class Importer:
    def __init__(self, store: FKTranslationStore, private_key=None, on_user_created=None):
        """``private_key`` unseals secret fields when supplied; ``on_user_created`` is called once
        per newly created user (e.g. to send an invite)."""
        self.store = store
        self.private_key = private_key
        self.on_user_created = on_user_created
        # The single team every resource is imported into. Captured from the team row (first in the
        # manifest) and assigned to every team-scoped row, since the per-row team FK isn't exported.
        self.target_team = None

    def import_rows(self, model_label: str, rows: Iterable[dict]) -> None:
        """Import every row for one model, unsealing its secret fields first when we hold the key."""
        model = entry_model(model_label)
        secret_fields = SECRET_REGISTRY.get(model_label, [])
        for row in rows:
            if self.private_key and secret_fields:
                row = unseal_secrets(row, secret_fields, self.private_key)
            self._import_row(model_label, model, row)

    def _import_row(self, model_label: str, model: type[models.Model], row: dict) -> None:
        """Import a single row. A global row is matched to its shared target and only its id
        translation is recorded; a team-owned row is created or updated, then its m2m and named
        links are applied and source timestamps restored."""
        global_spec = GLOBAL_CONFIG.get(model_label)
        source_pk = row["id"]

        # Global row (flagged by the export): match the existing target row by natural key and just
        # record the id translation -- these are shared, never recreated.
        if global_spec and row.get("is_global"):
            match = self._match_global(model, global_spec, row)
            if match is None:
                raise MissingGlobalRow(model_label, global_spec, row)
            self.store.record(model_label, source_pk, match.pk)
            return

        # Team-owned row. The create, its m2m/named links, the timestamp restore, and the checkpoint
        # fill all happen in one transaction so an interruption can't leave a committed row whose
        # checkpoint is still null -- which a rerun would re-fetch and duplicate. The checkpoint is
        # filled last: if anything before it fails, the row rolls back with it.
        instance, created = self._import_team_owned_row(model_label, model, source_pk, row)

        if created:
            if model_label == "users.customuser" and self.on_user_created:
                self.on_user_created(instance)
        if model_label == "teams.team":
            set_current_team(instance)
            self.target_team = instance

    def _import_team_owned_row(
        self, model_label: str, model: type[models.Model], source_pk: int, row: dict
    ) -> tuple[models.Model, bool]:
        with transaction.atomic():
            field_values, m2m_values, timestamps = self._build_values(model_label, model, row)
            instance, created = self._get_or_create(model_label, model, source_pk, row, field_values)

            for name, target_pks in m2m_values.items():
                getattr(instance, name).set([pk for pk in target_pks if pk is not None])

            self._apply_named_links(model_label, instance, row)

            if timestamps:  # keep the source timestamps; auto_now would otherwise overwrite them
                _bypass_auto_now_update(model, instance.pk, timestamps)

            self.store.record(model_label, source_pk, instance.pk)
        return instance, created

    def _get_or_create(
        self, model_label: str, model: type[models.Model], source_pk: int, row: dict, field_values: dict
    ) -> tuple[models.Model, bool]:
        """Find the existing target row (via the translation map, then a model-specific natural-key
        match) or create it. Returns (instance, created)."""
        target_pk = self.store.get_target(model_label, source_pk)
        instance = None
        if target_pk is not None and model.objects.filter(pk=target_pk).exists():
            instance = model.objects.get(pk=target_pk)
        elif model_label in _MATCH_EXISTING:
            instance = _MATCH_EXISTING[model_label](model, row, self.store, self.target_team)

        if instance is not None:
            if model_label not in _NO_UPDATE_MODELS:
                for key, value in field_values.items():
                    setattr(instance, key, value)
                instance.save()
            return instance, False

        self.store.record(model_label, source_pk)  # checkpoint marker before the row exists
        instance = model(**field_values)
        instance.save()
        return instance, True

    def _build_values(self, model_label: str, model: type[models.Model], row: dict) -> tuple[dict, dict, dict]:
        """Split a serialized row into (concrete field values, translated m2m pk lists, source
        timestamps). FKs are remapped to target ids, pipeline/node resource ids are rewritten, and
        named-link fields are left out for ``_apply_named_links`` to handle."""
        named = set(_NAMED_LINK_FIELDS.get(model_label, []))
        field_values, timestamps = {}, {}
        for field in model._meta.concrete_fields:
            self._collect_concrete_field(field, row, named, field_values, timestamps)
        self._remap_embedded_resource_ids(model_label, field_values)
        self._assign_team(model_label, model, field_values)
        m2m_values = self._build_m2m_values(model, row, named)
        return field_values, m2m_values, timestamps

    def _assign_team(self, model_label: str, model: type[models.Model], field_values: dict) -> None:
        """Set a team-scoped row's team to the team being synced. The per-row team FK isn't exported
        (everything belongs to one team), so it's assigned here from the team imported first."""
        if model_label == "teams.team" or not model_has_team_field(model):
            return
        if self.target_team is None:
            raise UnresolvedForeignKey(f"{model_label}.team: the team row must be imported before its data.")
        field_values["team_id"] = self.target_team.pk

    def _collect_concrete_field(self, field, row: dict, named: set, field_values: dict, timestamps: dict) -> None:
        """Route one concrete field into ``field_values`` or ``timestamps`` (or skip it). Source
        timestamps are held aside so ``auto_now`` doesn't clobber them; FKs are translated to target
        ids; named-link fields are left for ``_apply_named_links``."""
        if field.primary_key:
            return
        if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
            if field.name in row and row[field.name] is not None:
                timestamps[field.name] = parse_datetime(row[field.name])
        elif isinstance(field, models.ForeignKey):
            field_values[field.attname] = resolve_fk(field, row.get(field.name), self.store)
        elif field.name in row and field.name not in named:
            field_values[field.name] = row[field.name]

    def _remap_embedded_resource_ids(self, model_label: str, field_values: dict) -> None:
        """Rewrite the source resource ids buried in a pipeline's graph or a node's params in place."""
        if model_label == "pipelines.pipeline" and "data" in field_values:
            field_values["data"] = remap_pipeline_data(field_values["data"], self.store)
        elif model_label == "pipelines.node" and "params" in field_values:
            field_values["params"] = remap_node_params(field_values["params"], self.store)

    def _build_m2m_values(self, model: type[models.Model], row: dict, named: set) -> dict:
        """Translate each m2m field's source pks to target pks, skipping name-linked fields."""
        m2m_values = {}
        for field in model._meta.many_to_many:
            if field.name in row and field.name not in named:
                label = field.related_model._meta.label_lower
                m2m_values[field.name] = [self.store.get_target(label, as_int(pk)) for pk in row[field.name]]
        return m2m_values

    def _match_global(self, model: type[models.Model], spec: GlobalSpec, row: dict) -> models.Model | None:
        """Find the shared global row on the target by its natural key (scoping field null)."""
        lookup = {f"{spec.null_field}__isnull": True}
        lookup.update({key: row[key] for key in spec.natural_key})
        return model.objects.filter(**lookup).first()

    def _apply_named_links(self, model_label: str, instance: models.Model, row: dict) -> None:
        """Re-link the m2m fields serialized as names (see ``_NAMED_LINK_FIELDS``). Their targets --
        feature flags and auth groups -- aren't synced, so they're matched against existing target
        rows by name rather than by translated id. Names with no match are skipped."""
        if model_label == "teams.team":
            for name in row.get("feature_flags", []):
                flag = Flag.objects.filter(name=name).first()
                if flag:
                    flag.teams.add(instance)
        elif model_label == "teams.membership":
            instance.groups.set(Group.objects.filter(name__in=row.get("groups", [])))


def _bypass_auto_now_update(model: type[models.Model], pk: int, values: dict) -> None:
    """Write the source timestamps back. QuerySet.update() bypasses auto_now/auto_now_add; audited
    models additionally need an explicit (non-auditing) action."""
    queryset = model._default_manager.filter(pk=pk)
    if isinstance(queryset, AuditingQuerySet):
        queryset.update(audit_action=AuditAction.IGNORE, **values)
    else:
        queryset.update(**values)
