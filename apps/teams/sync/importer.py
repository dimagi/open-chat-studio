"""The import engine: pull a serialized row, rewrite every reference from source ids to target ids,
create or update the row locally, restore its timestamps, and record the FK translation. Kept as
plain functions plus a thin loop so the transforms can be unit-tested without a database."""

import copy
import functools
from collections.abc import Iterable

from django.db import models
from django.utils.dateparse import parse_datetime
from field_audit.models import AuditAction, AuditingQuerySet

from apps.utils.fields import as_int

from .manifest import GLOBAL_CONFIG, SECRET_REGISTRY, GlobalSpec, entry_model
from .manifest import MANIFEST_ENTRIES as _ENTRIES
from .seal import unseal
from .translation import FKTranslationStore

MANIFEST_LABELS = {entry.model for entry in _ENTRIES}


class UnresolvedForeignKey(Exception):
    """A required FK points at a row that should have been synced but wasn't (an ordering bug)."""


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


def _match_existing_user(model: type[models.Model], row: dict, store: FKTranslationStore) -> models.Model | None:
    """Map onto a user that already exists on the target rather than colliding on the unique username."""
    return model.objects.filter(username=row["username"]).first()


def _match_default_consent_form(model: type[models.Model], row: dict, store: FKTranslationStore) -> models.Model | None:
    """The target team's default consent form is auto-created on team creation; map the source's
    default onto it instead of creating a second one (which the per-team unique constraint forbids)."""
    if not row.get("is_default"):
        return None
    target_team = store.get_target("teams.team", row.get("team"))
    return model.objects.filter(team_id=target_team, is_default=True).first() if target_team else None


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

        # Global row (scoping field null): match the existing target row by natural key and just
        # record the id translation -- these are shared, never recreated.
        if global_spec and row.get(global_spec.null_field) is None:
            match = self._match_global(model, global_spec, row)
            if match is not None:
                self.store.record(model_label, source_pk, match.pk)
            return

        # Team-owned row: create or update it, then record the source->target id for later FK lookups.
        field_values, m2m_values, timestamps = self._build_values(model_label, model, row)
        instance, created = self._get_or_create(model_label, model, source_pk, row, field_values)
        self.store.record(model_label, source_pk, instance.pk)

        for name, target_pks in m2m_values.items():
            getattr(instance, name).set([pk for pk in target_pks if pk is not None])

        self._apply_named_links(model_label, instance, row)

        if timestamps:  # keep the source timestamps; auto_now would otherwise overwrite them
            _bypass_auto_now_update(model, instance.pk, timestamps)

        if created and model_label == "users.customuser" and self.on_user_created:
            self.on_user_created(instance)

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
            instance = _MATCH_EXISTING[model_label](model, row, self.store)

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
            if field.primary_key:
                continue
            if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
                if field.name in row and row[field.name] is not None:
                    timestamps[field.name] = parse_datetime(row[field.name])
                continue
            if isinstance(field, models.ForeignKey):
                field_values[field.attname] = resolve_fk(field, row.get(field.name), self.store)
            elif field.name in row and field.name not in named:
                field_values[field.name] = row[field.name]

        if model_label == "pipelines.pipeline" and "data" in field_values:
            field_values["data"] = remap_pipeline_data(field_values["data"], self.store)
        elif model_label == "pipelines.node" and "params" in field_values:
            field_values["params"] = remap_node_params(field_values["params"], self.store)

        m2m_values = {}
        for field in model._meta.many_to_many:
            if field.name in row and field.name not in named:
                label = field.related_model._meta.label_lower
                m2m_values[field.name] = [self.store.get_target(label, as_int(pk)) for pk in row[field.name]]
        return field_values, m2m_values, timestamps

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
            from apps.teams.models import Flag  # noqa: PLC0415

            for name in row.get("feature_flags", []):
                flag = Flag.objects.filter(name=name).first()
                if flag:
                    flag.teams.add(instance)
        elif model_label == "teams.membership":
            from django.contrib.auth.models import Group  # noqa: PLC0415

            instance.groups.set(Group.objects.filter(name__in=row.get("groups", [])))


def _bypass_auto_now_update(model: type[models.Model], pk: int, values: dict) -> None:
    """Write the source timestamps back. QuerySet.update() bypasses auto_now/auto_now_add; audited
    models additionally need an explicit (non-auditing) action."""
    queryset = model._default_manager.filter(pk=pk)
    if isinstance(queryset, AuditingQuerySet):
        queryset.update(audit_action=AuditAction.IGNORE, **values)
    else:
        queryset.update(**values)
