"""A dynamically built DRF ModelSerializer per synced model, so a serializer can't drift from its
model and a new field is exported the moment it's added. Output-only; ``.save()`` is never called."""

from functools import cache

from drf_spectacular.utils import OpenApiResponse, extend_schema_field
from rest_framework import serializers

from apps.teams.export.manifest import (
    EXCLUDE_REGISTRY,
    GLOBAL_CONFIG,
    SECRET_REGISTRY,
    TEAM_MODEL,
    ManifestEntry,
    entry_model,
    model_has_team_field,
)
from apps.teams.export.seal import seal


class _SecretMixin:
    secret_fields: list[str] = []

    def to_representation(self, instance):
        data = super().to_representation(instance)
        public_key = self.context.get("public_key")
        for field in self.secret_fields:
            data[field] = seal(getattr(instance, field), public_key)
        return data


@extend_schema_field(serializers.ListField(child=serializers.CharField()))
def _feature_flags(self, team):
    from apps.teams.models import Flag  # noqa: PLC0415 - avoid import cycle at module load

    return list(Flag.objects.filter(teams=team).values_list("name", flat=True))


@extend_schema_field(serializers.ListField(child=serializers.CharField()))
def _team_role_groups(self, user):
    """The user's role in the exported team: the group names on their membership for that team. Reads
    the prefetched memberships, so no per-row query. Empty when no team is in the context."""
    team = self.context.get("team")
    team_id = team.id if team is not None else None
    for membership in user.membership_set.all():
        if membership.team_id == team_id:
            return [group.name for group in membership.groups.all()]
    return []


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
    # Cached so every caller (the export resource endpoint and the v2 chatbot retrieve) shares one
    # serializer class per model. drf-spectacular keys components by class identity, so a fresh class
    # per call would collide on the component name and produce an incorrect schema.
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

    return type(f"{component_name(model)}DetailSerializer", (_SecretMixin, serializers.ModelSerializer), attrs)


def build_team_serializer():
    """Serializer for the single-team endpoint (``GET /api/export/team/``). The team anchors the export
    surface and is served as one object rather than a page, but its fields are the same dynamic dump
    as any other synced resource row."""
    return build_resource_serializer(entry_model(TEAM_MODEL))


def resource_responses(entry: ManifestEntry) -> dict[int, type[serializers.Serializer] | OpenApiResponse]:
    """Per-status responses for one resource: the 200 envelope, and a 409 (secret resources only)
    when the team has no public key to seal against. No 404: routing prevents unknown resources from
    reaching the view."""
    responses: dict[int, type[serializers.Serializer] | OpenApiResponse] = {
        200: build_resource_response_serializer(entry),
    }
    if entry.secret:
        responses[409] = OpenApiResponse(
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
