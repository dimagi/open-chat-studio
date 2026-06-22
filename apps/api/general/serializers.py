"""A dynamically built DRF ModelSerializer per synced model, so a serializer can't drift from its
model and a new field is exported the moment it's added. Output-only; ``.save()`` is never called."""

from rest_framework import serializers

from apps.teams.sync.manifest import EXCLUDE_REGISTRY, SECRET_REGISTRY
from apps.teams.sync.seal import seal


class _SyncSecretMixin:
    secret_fields: list[str] = []

    def to_representation(self, instance):
        data = super().to_representation(instance)
        public_key = self.context.get("public_key")
        for field in self.secret_fields:
            data[field] = seal(getattr(instance, field), public_key)
        return data


def _feature_flags(self, team):
    from apps.teams.models import Flag  # noqa: PLC0415 - avoid import cycle at module load

    return list(Flag.objects.filter(teams=team).values_list("name", flat=True))


def _group_names(self, instance):
    return list(instance.groups.values_list("name", flat=True))


# Per-model SerializerMethodFields for values that aren't a plain field dump.
_METHOD_FIELDS: dict[str, dict] = {
    "teams.team": {"feature_flags": _feature_flags},
    "teams.membership": {"groups": _group_names},
}


def build_sync_serializer(model):
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
    for name, method in _METHOD_FIELDS.get(label, {}).items():
        attrs[name] = serializers.SerializerMethodField()
        attrs[f"get_{name}"] = method

    return type(f"{model.__name__}SyncSerializer", (_SyncSecretMixin, serializers.ModelSerializer), attrs)
