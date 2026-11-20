"""Assertions over a versioned record and one of its versions, for every app's versioning tests."""

from django.db.models import Field

from apps.experiments.versioning import VersionSpec


def travelling_fields(instance) -> list[Field]:
    """The concrete fields a version of `instance` carries: everything but the identity its spec names.

    A model without a spec yet falls back to the fields it excludes from version comparison."""
    spec = VersionSpec.for_model(type(instance))
    identity = spec.identity if spec else {*instance.get_fields_to_exclude(), "is_archived"}
    return [field for field in instance._meta.concrete_fields if field.name not in identity]


def assert_snapshot(working, version, *, changed: set[str] | frozenset[str] = frozenset()) -> None:
    """Both directions: every field in `changed` differs between the rows, every other travelling field is equal."""
    fields = travelling_fields(working)
    unknown = set(changed) - {field.name for field in fields}
    assert not unknown, f"{sorted(unknown)} are not travelling fields of {type(working).__name__}"
    for field in fields:
        on_working = getattr(working, field.attname)
        on_version = getattr(version, field.attname)
        if field.name in changed:
            assert on_working != on_version, f"{field.name} was expected to change but is {on_working!r} on both rows"
        else:
            assert on_working == on_version, (
                f"{field.name} differs: {on_working!r} on the working row, {on_version!r} on the version"
            )


def assert_family(version, working) -> None:
    """`version` is a version of `working`: linked to it, numbered below it when the model numbers versions."""
    assert version.working_version_id == working.pk
    assert version.is_a_version
    assert working.is_working_version
    if hasattr(working, "version_number"):
        assert version.version_number < working.version_number
