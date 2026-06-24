"""A dynamically built DRF ModelSerializer per synced model, so a serializer can't drift from its
model and a new field is exported the moment it's added. Output-only; ``.save()`` is never called."""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.teams.export.manifest import EXCLUDE_REGISTRY, GLOBAL_CONFIG, SECRET_REGISTRY, model_has_team_field
from apps.teams.export.seal import seal


class _SyncSecretMixin:
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
def _group_names(self, instance):
    return [group.name for group in instance.groups.all()]


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
    "teams.membership": {"groups": _group_names},
}


class ManifestEntrySerializer(serializers.Serializer):
    """One row of the manifest: which model to pull and the config the endpoint/importer need."""

    model = serializers.CharField(help_text="Internal `app_label.model` key.")
    resource = serializers.CharField(help_text="URL-facing resource name the endpoint is mounted at.")
    phase = serializers.CharField(help_text="structural | live | structural+live.")
    cursor = serializers.CharField(help_text="Pagination cursor type: pk | updated_at_id.")
    secret = serializers.BooleanField(help_text="Whether rows carry fields sealed under the team public key.")


class ManifestSerializer(serializers.Serializer):
    """The sync manifest: the call order and per-model config, plus a checksum of the applied schema."""

    schema_checksum = serializers.CharField()
    entries = ManifestEntrySerializer(many=True)


def build_resource_serializer(model):
    label = model._meta.label_lower
    # Every resource is imported into the single team named on the sync command, so the per-row team
    # FK is redundant -- drop it. Global/shared models surface an `is_global` flag instead, since
    # their team being null is what marks them shared.
    exclude = list(EXCLUDE_REGISTRY.get(label, []))
    if model_has_team_field(model):
        exclude.append("team")
    meta_attrs = {"model": model, "exclude": exclude} if exclude else {"model": model, "fields": "__all__"}

    attrs: dict[str, object] = {
        "Meta": type("Meta", (), meta_attrs),
        "secret_fields": SECRET_REGISTRY.get(label, []),
    }
    for name, method in _FIELD_RESOLVERS.get(label, {}).items():
        attrs[name] = serializers.SerializerMethodField()
        attrs[f"get_{name}"] = method

    spec = GLOBAL_CONFIG.get(label)
    if spec:
        attrs["is_global"] = serializers.SerializerMethodField()
        attrs["get_is_global"] = _is_global_resolver(spec.null_field)

    return type(f"{model.__name__}SyncSerializer", (_SyncSecretMixin, serializers.ModelSerializer), attrs)
