"""A dynamically built DRF ModelSerializer per synced model, so a serializer can't drift from its
model and a new field is exported the moment it's added. Output-only; ``.save()`` is never called."""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.teams.export.manifest import EXCLUDE_REGISTRY, SECRET_REGISTRY
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
    order_by = serializers.CharField(
        required=False, allow_null=True, help_text="Optional ordering applied before paging."
    )


class ManifestSerializer(serializers.Serializer):
    """The sync manifest: the call order and per-model config, plus a checksum of the applied schema."""

    schema_checksum = serializers.CharField()
    entries = ManifestEntrySerializer(many=True)


def build_resource_serializer(model):
    label = model._meta.label_lower
    meta_attrs = {"model": model}
    if label in EXCLUDE_REGISTRY:
        meta_attrs["exclude"] = EXCLUDE_REGISTRY[label]
    else:
        meta_attrs["fields"] = "__all__"

    attrs: dict[str, object] = {
        "Meta": type("Meta", (), meta_attrs),
        "secret_fields": SECRET_REGISTRY.get(label, []),
    }
    for name, method in _FIELD_RESOLVERS.get(label, {}).items():
        attrs[name] = serializers.SerializerMethodField()
        attrs[f"get_{name}"] = method

    return type(f"{model.__name__}SyncSerializer", (_SyncSecretMixin, serializers.ModelSerializer), attrs)
