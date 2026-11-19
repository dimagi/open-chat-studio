"""System checks for `VersionSpec` declarations, run by `manage.py check` under the `models` tag."""

from django.apps import apps
from django.core import checks
from django.core.exceptions import FieldDoesNotExist

from .versioning import Reference, Strategy, VersionsMixin, VersionSpec

FAMILY_COLUMNS = ("working_version", "is_archived")


@checks.register(checks.Tags.models)
def check_version_specs(app_configs=None, **kwargs) -> list[checks.CheckMessage]:
    messages: list[checks.CheckMessage] = []
    for spec_cls in VersionSpec.registry.values():
        messages.extend(check_spec(spec_cls))
    messages.extend(check_versioned_models())
    return messages


def check_spec(spec_cls: type[VersionSpec]) -> list[checks.CheckMessage]:
    """E001 to E004 for one spec: its members, its identity and its model all have to exist and agree."""
    label = spec_cls.Meta.model
    try:
        model = apps.get_model(label)
    except (LookupError, ValueError):
        return [_error(spec_cls, f"Meta.model {label!r} does not resolve to a model", "E004")]

    forward = {field.name for field in model._meta.get_fields() if not (field.is_relation and field.auto_created)}
    messages: list[checks.CheckMessage] = []

    identity = frozenset(getattr(spec_cls.Meta, "identity", ()))
    regenerated = dict(getattr(spec_cls.Meta, "regenerated", {}))
    for attribute, names in (("identity", identity), ("regenerated", regenerated.keys())):
        messages.extend(
            _error(spec_cls, f"Meta.{attribute} names {name!r}, which is not a field of {label}", "E004")
            for name in sorted(names - forward)
        )

    for member in spec_cls.members:
        messages.extend(_error(spec_cls, message, code) for code, message in member.check(model))

    declared = {member.name for member in spec_cls.members}
    for field in model._meta.concrete_fields:
        target = field.related_model
        versioned = isinstance(target, type) and issubclass(target, VersionsMixin)
        if not ((field.many_to_one or field.one_to_one) and versioned):
            continue
        if field.name in identity or field.name in declared:
            continue
        messages.append(
            _error(
                spec_cls,
                f"{label}.{field.name} points at the versioned model {target._meta.label} but no member "
                "declares how it travels, so a working id would be cloned into every snapshot",
                "E002",
            )
        )

    provided: set[str] = set()
    for member in spec_cls.members:
        messages.extend(
            _error(spec_cls, f"{member.name} reads {key!r}, which no earlier member provides", "E003")
            for key in member.reads
            if key not in provided
        )
        if member.provides:
            provided.add(member.provides)
    return messages


def check_versioned_models() -> list[checks.CheckMessage]:
    """W001: a versioned model without the family columns, unless a spec clones it privately."""
    privately_cloned = {
        _referenced_label(spec_cls.Meta.model, member.name)
        for spec_cls in VersionSpec.registry.values()
        for member in spec_cls.members
        if isinstance(member, Reference) and member.strategy is Strategy.PRIVATE_CLONE
    }
    messages: list[checks.CheckMessage] = []
    for model in apps.get_models():
        if not issubclass(model, VersionsMixin) or model._meta.label_lower in privately_cloned:
            continue
        present = {field.name for field in model._meta.concrete_fields}
        missing = [column for column in FAMILY_COLUMNS if column not in present]
        if missing:
            messages.append(
                checks.Warning(
                    f"{model._meta.label} mixes in VersionsMixin without {', '.join(missing)}, which the "
                    "inherited archive and family helpers read",
                    obj=model,
                    id="versioning.W001",
                )
            )
    return messages


def _referenced_label(model_label: str, field_name: str) -> str | None:
    """The label of the model `field_name` points at; None for a declaration `check_spec` has already reported."""
    try:
        return apps.get_model(model_label)._meta.get_field(field_name).related_model._meta.label_lower
    except (LookupError, ValueError, FieldDoesNotExist, AttributeError):
        return None


def _error(spec_cls, message: str, code: str) -> checks.Error:
    return checks.Error(f"{spec_cls.__name__}: {message}", obj=spec_cls, id=f"versioning.{code}")
