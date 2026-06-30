"""A dynamically built DRF ModelSerializer per synced model, so a serializer can't drift from its
model and a new field is exported the moment it's added. Output-only; ``.save()`` is never called."""

from functools import cache

import pydantic
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django_pydantic_field.fields import PydanticSchemaField
from drf_spectacular.utils import OpenApiResponse, extend_schema_field
from pgvector.django import HalfVectorField, VectorField
from rest_framework import serializers

from apps.teams.export.manifest import (
    EXCLUDE_REGISTRY,
    GLOBAL_CONFIG,
    SECRET_REGISTRY,
    TEAM_MODEL,
    ManifestEntry,
    entry_model,
    generic_fk_fields,
    model_has_team_field,
)
from apps.teams.export.seal import seal
from apps.teams.models import Flag


def _json_safe(value):
    """Reduce a pydantic object (e.g. CollectionFile.metadata, DocumentSource.config) to plain JSON
    data. Non-pydantic values are already JSON-native and pass through."""
    return value.model_dump(mode="json") if isinstance(value, pydantic.BaseModel) else value


class _RelativeFileField(serializers.FileField):
    """Serialize a file as its stored relative path (``value.name``) rather than the default
    ``/media/...`` URL. The URL is a presentation form; the importer needs the raw name so it
    assigns straight back into a FileField -- an absolute ``/media/`` path is rejected by Django's
    storage safe-join (SuspiciousFileOperation)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("use_url", False)
        super().__init__(*args, **kwargs)


@extend_schema_field(serializers.ListField(child=serializers.FloatField()))
class _VectorField(serializers.Field):
    """Serialize a pgvector column as a plain list of floats. Its DB string form (e.g. ``[0.1,0.2]``)
    isn't accepted as input, so the importer needs the list to assign it straight back."""

    def to_representation(self, value):
        # A HalfVectorField reads back as a pgvector HalfVector object, which isn't iterable but
        # exposes .to_list(); a VectorField reads back as an (iterable) numpy array.
        if hasattr(value, "to_list"):
            return value.to_list()
        return [float(x) for x in value]

    def to_internal_value(self, data):
        return data


class _PydanticSchemaField(serializers.JSONField):
    """Serialize a django_pydantic_field SchemaField to a plain JSON object. The default JSONField
    mapping leaves the pydantic model instance in place, which DRF's JSON encoder then mangles into a
    list of (field, value) pairs (it falls back to iterating the model) that the importer can't read
    back into the schema. Subclasses JSONField so DRF's encoder/decoder kwargs are still accepted."""

    def to_representation(self, value):
        return _json_safe(value)


class _SecretMixin:
    secret_fields: list[str] = []

    def to_representation(self, instance):
        data = super().to_representation(instance)
        public_key = self.context.get("public_key")
        for field in self.secret_fields:
            data[field] = seal(_json_safe(getattr(instance, field)), public_key)
        return data


@extend_schema_field(serializers.ListField(child=serializers.CharField()))
def _feature_flags(self, team):
    return list(Flag.objects.filter(teams=team).values_list("name", flat=True))


@extend_schema_field(serializers.ListField(child=serializers.CharField()))
def _team_role_groups(self, user):
    """The user's role in the exported team: the group names on their membership for that team. The
    export queryset prefetches only the exported team's membership, filtered in the DB (see
    ``_customuser_prefetch``), so this reads the cache without a per-row query. The ``team_id`` guard
    keeps the field correct when a user is serialized outside that queryset (e.g. in unit tests).
    Empty when no team is in the context."""
    team = self.context.get("team")
    team_id = team.id if team is not None else None
    for membership in user.membership_set.all():
        if membership.team_id == team_id:
            return [group.name for group in membership.groups.all()]
    return []


def _content_type_label_resolver(ct_field: str):
    """Build the method that emits a generic FK's content type as its ``app_label.model`` label
    instead of the source ContentType pk (which differs between servers). Reads the ``*_id`` attribute
    and looks the type up by id (cached), so it never triggers a related-object query."""
    attname = f"{ct_field}_id"

    @extend_schema_field(serializers.CharField())
    def get_content_type_label(self, instance) -> str | None:
        type_id = getattr(instance, attname)
        if type_id is None:
            return None
        ct = ContentType.objects.get_for_id(type_id)
        return f"{ct.app_label}.{ct.model}"

    return get_content_type_label


def _is_global_resolver(null_field: str):
    """Build the ``is_global`` method: a row is global when its scoping field (team or voice
    provider) is null. Reads the ``*_id`` attribute so it never triggers a related-object query."""
    attname = f"{null_field}_id"

    def get_is_global(self, instance) -> bool:
        return getattr(instance, attname) is None

    return get_is_global


# Per-model SerializerMethodFields for values that aren't a plain field dump.
_FIELD_RESOLVERS: dict[str, dict] = {
    "teams.team": {"feature_flags": _feature_flags},
    "users.customuser": {"groups": _team_role_groups},
}


class ManifestEntrySerializer(serializers.Serializer):
    """One row of the manifest: which model to pull and the config the endpoint/importer need."""

    model = serializers.CharField(help_text="Internal `app_label.model` key.")
    resource = serializers.CharField(help_text="URL-facing resource name the endpoint is mounted at.")
    cursor = serializers.CharField(help_text="Pagination cursor type: pk | updated_at_id.")
    secret = serializers.BooleanField(help_text="Whether rows carry fields sealed under the team public key.")


class ManifestSerializer(serializers.Serializer):
    """The sync manifest: the call order and per-model config, plus a checksum of the applied schema."""

    schema_checksum = serializers.CharField()
    entries = ManifestEntrySerializer(many=True)


def component_name(model) -> str:
    """The base name for a synced model's OpenAPI components: its ``verbose_name`` PascalCased."""
    return "".join(word[:1].upper() + word[1:] for word in str(model._meta.verbose_name).split())


@cache
def build_resource_serializer(model):
    # Cached so repeated calls for the same model share one serializer class. drf-spectacular keys
    # components by class identity, so a fresh class per call would collide on the component name and
    # produce an incorrect schema.
    label = model._meta.label_lower
    # Every resource is imported into the single team named on the sync command, so the per-row team
    # FK is redundant -- drop it. Global/shared models surface an `is_global` flag instead, since
    # their team being null is what marks them shared.
    exclude = list(EXCLUDE_REGISTRY.get(label, []))
    if model_has_team_field(model):
        exclude.append("team")
    meta_attrs = {"model": model, "exclude": exclude} if exclude else {"model": model, "fields": "__all__"}

    secret_fields = SECRET_REGISTRY.get(label, [])
    attrs: dict[str, object] = {
        "Meta": type("Meta", (), meta_attrs),
        "secret_fields": secret_fields,
        "serializer_field_mapping": {
            **serializers.ModelSerializer.serializer_field_mapping,
            models.FileField: _RelativeFileField,
            models.ImageField: _RelativeFileField,
            HalfVectorField: _VectorField,
            VectorField: _VectorField,
            PydanticSchemaField: _PydanticSchemaField,
        },
    }
    # Secret fields are sealed to a base64 string at runtime (see _SecretMixin), so document them as
    # that string rather than their raw model field type.
    for field in secret_fields:
        attrs[field] = serializers.CharField(help_text="Sealed under the team's public key (base64, RSA-OAEP).")
    for name, method in _FIELD_RESOLVERS.get(label, {}).items():
        attrs[name] = serializers.SerializerMethodField()
        attrs[f"get_{name}"] = method

    if spec := GLOBAL_CONFIG.get(label):
        attrs["is_global"] = serializers.SerializerMethodField()
        attrs["get_is_global"] = _is_global_resolver(spec.null_field)

    # A generic FK's content type goes out as its model label, not the source ContentType pk. The
    # object-id column keeps its default (the source target-row pk); the importer resolves both.
    for ct_field, _fk_field in generic_fk_fields(model):
        attrs[ct_field] = serializers.SerializerMethodField()
        attrs[f"get_{ct_field}"] = _content_type_label_resolver(ct_field)

    return type(f"{component_name(model)}DetailSerializer", (_SecretMixin, serializers.ModelSerializer), attrs)


def build_team_serializer():
    """Serializer for the single-team endpoint (``GET /api/export/team/``). The team anchors the export
    surface and is served as one object rather than a page, but its fields are the same dynamic dump
    as any other synced resource row."""
    return build_resource_serializer(entry_model(TEAM_MODEL))


def resource_responses(entry: ManifestEntry) -> dict[int, type[serializers.Serializer] | OpenApiResponse]:
    """Per-status responses for one resource: the 200 envelope, and a 400 (secret resources only)
    when the team has no public key to seal against. No 404: routing prevents unknown resources from
    reaching the view."""
    responses: dict[int, type[serializers.Serializer] | OpenApiResponse] = {
        200: build_resource_response_serializer(entry),
    }
    if entry.secret:
        responses[400] = OpenApiResponse(
            response=SyncErrorDetail,
            description="Team has no registered public key; secret data cannot be sealed.",
        )
    return responses


def build_resource_response_serializer(entry: ManifestEntry) -> type[serializers.Serializer]:
    """The paginated envelope the resource endpoint returns: cursor, has_more, and the page of rows."""
    model = entry_model(entry.model)
    item = build_resource_serializer(model)
    return type(
        f"{component_name(model)}List",
        (serializers.Serializer,),
        {
            "cursor": serializers.CharField(
                allow_null=True, help_text="Cursor for the next page; null once the resource is exhausted."
            ),
            "has_more": serializers.BooleanField(help_text="True while more rows remain beyond this page."),
            "results": item(many=True),
        },
    )


class SyncErrorDetail(serializers.Serializer):
    detail = serializers.CharField()
