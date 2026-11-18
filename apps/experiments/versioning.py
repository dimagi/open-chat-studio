import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as data_field
from enum import StrEnum
from functools import cached_property
from itertools import chain
from typing import Any, ClassVar, Self

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from django.db.models import (
    BooleanField,
    Case,
    Field,
    Q,
    QuerySet,
    When,
)

from apps.utils.models import VersioningMixin

versioning_logger = logging.getLogger("ocs.versioning")


def differs(original: Any, new: Any, exclude_model_fields: list[str] | None = None, early_abort=False) -> bool:
    """
    Compares the value (or attributes in the case of a Model) between `original` and `new`.
    Returns `True` if it differs and `False` if not.

    When comparing models, fields in `exclude_model_fields` will be excluded.
    """
    exclude_model_fields = exclude_model_fields or []
    if isinstance(original, VersioningMixin) and isinstance(new, VersioningMixin):
        return bool(original.compare_with_model(new, exclude_model_fields, early_abort=early_abort))
    return original != new


def default_to_display(value):
    """The default function to use to display the value of a field or queryset"""
    return value


@dataclass
class FieldGroup:
    name: str
    fields: list["VersionField"] = data_field(default_factory=list)
    has_fields_with_values: bool = data_field(default=False)
    # Indicates whether a field in this group changed
    has_changed_fields: bool = data_field(default=False)


@dataclass
class VersionField:
    """Represents a specific detail about the instance. The label is the user friendly name"""

    NUM_QUERYSET_RESULTS_TO_SHOW = 10

    name: str = ""
    raw_value: Any | None = None
    to_display: callable = None
    group_name: str = data_field(default="General")
    previous_field_version: "VersionField | None" = data_field(default=None)
    changed: bool = False
    label: str = data_field(default="")
    queryset: QuerySet | None = None
    raw_value_version: Self | None = None
    num_results_truncated: int = 0

    @property
    def current_value(self):
        return self.raw_value

    @property
    def previous_value(self):
        return self.previous_field_version.raw_value

    @property
    def is_a_version(self):
        return self.raw_value_version is not None

    @cached_property
    def queryset_results(self) -> list["VersionField"]:
        if self.current_value and not hasattr(self.current_value, "version_details"):
            return []

        if self.queryset is None:
            return []

        return [VersionField(raw_value=record, to_display=self.to_display) for record in self.queryset.all()]

    @property
    def queryset_results_for_display(self) -> list["VersionField"]:
        """
        This method is used to display queryset results in the version comparison UI. It limits the number of results
        shown for performance reasons.
        """
        # Start by showing those fields that changed
        self.num_results_truncated = max(0, self.queryset.count() - self.NUM_QUERYSET_RESULTS_TO_SHOW)
        changed_results = sorted(self.queryset_results, key=lambda x: x.changed, reverse=True)
        return changed_results[: self.NUM_QUERYSET_RESULTS_TO_SHOW]

    def __post_init__(self):
        self.label = self.name.replace("_", " ").title()

    def display_value(self) -> Any:
        to_display = self.to_display or default_to_display
        if self.queryset is not None:
            return to_display(self.queryset_results)
        if self.current_value is not None:
            return to_display(self.current_value)
        return ""

    def _get_fields_to_exclude(self) -> list[str]:
        if self.current_value:
            return self.current_value.get_fields_to_exclude()
        return self.previous_value.get_fields_to_exclude()

    def compare(self, previous_field_version: "VersionField", early_abort=False):
        """
        Args:
            previous_field_version (VersionField): The previous version field to compare against.
            early_abort (bool): If True, the comparison will stop as soon as the first difference is found.
        """

        self.previous_field_version = previous_field_version
        match self._get_field_type():
            case "unversioned_model":
                # Simply comparing unversioned models by id is enough
                current_id = self.current_value.id if self.current_value else None
                previous_id = self.previous_value.id if self.previous_value else None
                self.changed = current_id != previous_id
            case "versioned_model":
                # Versioned models should be explored in order to determine what changed
                if hasattr(self.current_value, "version_details"):
                    # Compare only the fields specified in the returned version
                    current_version = self.current_value.version_details if self.current_value else None
                    previous_version = self.previous_value.version_details if self.previous_value else None
                    if current_version and previous_version:
                        # Only when there is a previous and current version can we compare
                        current_version.compare(previous_version, early_abort=early_abort)
                        self.changed = current_version.fields_changed
                        self.raw_value_version = current_version
                        self.previous_field_version.raw_value_version = previous_version
                    else:
                        self.changed = True
                else:
                    # Compare all fields
                    changed_fields = self.current_value.compare_with_model(
                        self.previous_value, self._get_fields_to_exclude(), early_abort=early_abort
                    )
                    self.changed = bool(changed_fields)
            case "queryset":
                self._compare_querysets(early_abort)
            case "primitive":
                self.changed = self.current_value != self.previous_value

    def _get_field_type(self):
        if isinstance(self.current_value, VersioningMixin):
            if hasattr(self.current_value, "working_version"):
                return "versioned_model"
            else:
                return "unversioned_model"
        elif self.queryset is not None:
            return "queryset"
        else:
            return "primitive"

    @staticmethod
    def _group_records_by_family(version_fields: list["VersionField"]) -> dict[int, list]:
        """Group records by version family (records in the same family share a working version id)"""
        records_by_family: dict[int, list] = defaultdict(list)
        for version_field in version_fields:
            record = version_field.raw_value
            records_by_family[VersionField._get_family_id(record)].append(record)
        return records_by_family

    @staticmethod
    def _get_family_id(record) -> int:
        """Return the working version id for versioned records, or the record's own id for unversioned ones"""
        if isinstance(record, VersionsMixin):
            return record.get_working_version_id()
        return record.id

    @staticmethod
    def _pop_family_record(records_by_family: dict[int, list], record):
        """Removes and returns a record from the same version family as `record`, or None if there is none."""
        family_records = records_by_family.get(VersionField._get_family_id(record))
        return family_records.pop(0) if family_records else None

    def _compare_querysets(self, early_abort=False):
        """
        Compares two querysets by checking the differences between their results.

        For each result in the current queryset, this method attempts to find a corresponding version
        of that result in the previous queryset. If such a version exists, it is used for comparison.
        If no corresponding version is found, it indicates that the result was newly added.

        To identify results that have been removed, this method collects all records from the previous
        queryset that do not have a matching version in the current queryset.

        To ensure accurate comparisons, this method hinges on the "version family" concept. A result in the
        current queryset is only compared to a result in the previous queryset if they belong to the
        same "version family". This relationship is identified through the `working_version_id` field of each record,
        which is expected to be present on each result.
        """
        previous_records_by_family = self._group_records_by_family(self.previous_field_version.queryset_results)

        for version_field in self.queryset_results:
            previous_record = self._pop_family_record(previous_records_by_family, version_field.raw_value)
            if previous_record:
                # A version of the current record exists in the previous queryset
                prev_version_field = VersionField(raw_value=previous_record, to_display=self.to_display)
                version_field.compare(prev_version_field, early_abort=early_abort)
                self.changed = self.changed or version_field.changed
            else:
                # No version of the current record exists in the previous queryset, thus it is new
                version_field.changed = self.changed = True

            if early_abort and self.changed:
                return

        previous_records = list(chain.from_iterable(previous_records_by_family.values()))
        if previous_records:
            self.changed = True
            if early_abort:
                return

        for record in previous_records:
            # We need to add version fields for each removed record, but with the current value set to None
            prev_version_field = VersionField(raw_value=record, to_display=self.to_display)
            version_field = VersionField(raw_value=None, previous_field_version=prev_version_field, changed=True)
            self.queryset_results.append(version_field)


@dataclass
class VersionDetails:
    instance: Any
    fields: list[VersionField]
    fields_changed: bool = False
    previous_instance: Any = data_field(default=None)
    _fields_dict: dict = data_field(default_factory=dict)

    def __post_init__(self):
        for version_field in self.fields:
            self._fields_dict[version_field.name] = version_field

    def get_field(self, field_name: str) -> VersionField | None:
        return self._fields_dict.get(field_name)

    @property
    def fields_grouped(self):
        groups = defaultdict(dict)
        for field in self.fields:
            group_name = field.group_name
            group_info = groups.setdefault(group_name, FieldGroup(name=group_name))
            group_info.has_fields_with_values = (
                group_info.has_fields_with_values
                or bool(field.raw_value)
                or bool(field.changed)
                or bool(field.queryset_results)
            )
            group_info.has_changed_fields = group_info.has_changed_fields or field.changed
            group_info.fields.append(field)
        return list(groups.values())

    def compare(self, previous_version_details: "VersionDetails", early_abort: bool = False):
        """
        Compares the current instance with the previous version and updates the changed status of fields.

        Args:
            previous_version_details (Version): The previous version details to compare against.
            early_abort (bool): If True, the comparison will stop as soon as the first difference is found.
        """
        previous_instance = previous_version_details.instance

        if type(previous_instance) != type(self.instance):  # noqa: E721
            prev_instance_type = type(self.previous_instance)
            curr_instance_type = type(self.instance)
            raise TypeError(
                f"Cannot compare instances of different types: {curr_instance_type} and {prev_instance_type}."
            )
        self.previous_instance = previous_instance

        for field in self.fields:
            previous_field_version = previous_version_details.get_field(field.name)
            if not previous_field_version:
                # When a new field was added to the new version, we need to compare it to a None value in the previous
                # version
                previous_field_version = VersionField(
                    name=field.name,
                    raw_value=None,
                    queryset=None,
                    to_display=field.to_display,
                    group_name=field.group_name,
                )

            field.compare(previous_field_version, early_abort=early_abort)
            self.fields_changed = self.fields_changed or field.changed
            if field.changed and early_abort:
                return

        for previous_field in previous_version_details.fields:
            # When a field was totally removed from the new instance, we still need to track an empty value for it
            current_field_version = self.get_field(previous_field.name)
            if not current_field_version:
                missing_field = VersionField(
                    name=previous_field.name,
                    raw_value=None,
                    queryset=None,
                    to_display=previous_field.to_display,
                    group_name=previous_field.group_name,
                )
                self.fields.append(missing_field)
                self._fields_dict[missing_field.name] = missing_field
                missing_field.compare(previous_field, early_abort=early_abort)
                if early_abort:
                    return


class Operation(StrEnum):
    """What a traversal of a versioned record does."""

    PUBLISH = "publish"
    """working -> version"""

    REVERT = "revert"
    """version -> working"""

    COPY = "copy"
    """working -> new working"""


class Strategy(StrEnum):
    """How a referenced record travels when the record holding the reference is published."""

    NEW_VERSION = "new_version"
    """A new version of the working target."""

    REUSE_UNCHANGED = "reuse_unchanged"
    """The target's latest version when nothing changed since it, otherwise a new one."""

    LIVE_REFERENCE = "live_reference"
    """The working target itself; the version reads it live (ADR-0031)."""

    PRIVATE_CLONE = "private_clone"
    """A clone with no family link, owned by the row that references it."""


class Archiving(StrEnum):
    """What happens to a referenced record when a version that references it is archived."""

    ARCHIVE = "archive"
    """Archive the referenced record."""

    ARCHIVE_VERSIONS_ONLY = "archive_versions_only"
    """Archive the referenced record only when it is a version, never a live working record."""

    KEEP = "keep"
    """Leave the referenced record alone."""


class VersionSpecError(Exception):
    """A spec is declared, or traversed, in a way that cannot produce a correct snapshot."""


def reuse_or_version(target):
    """The target's latest version when nothing travelling changed since it, otherwise a new version of it."""
    latest = target.latest_version
    if latest and not differs(target, latest, exclude_model_fields=latest.get_fields_to_exclude()):
        return latest
    return target.create_new_version()


def model_names(model) -> tuple[dict[str, Field], set[str]]:
    """The forward fields of `model` by name, and the names of its reverse accessors."""
    fields = model._meta.get_fields()
    forward = {field.name: field for field in fields if not (field.is_relation and field.auto_created)}
    accessors = {field.get_accessor_name() for field in fields if field.is_relation and field.auto_created} - {None}
    return forward, accessors


class Member:
    """One declared part of a `VersionSpec`; subclasses say how it travels on each operation.

    `provides` names a context key the member writes its result to; `reads` names keys an earlier
    member must have provided, so a member declared before one it depends on raises `VersionSpecError`."""

    name: str

    def __init__(self, *, reads: tuple[str, ...] = (), provides: str | None = None):
        self.reads = tuple(reads)
        self.provides = provides

    def __set_name__(self, owner, name: str):
        self.name = name

    def publish(self, working, version, context: dict[str, Any]) -> None:
        raise NotImplementedError

    def copy(self, working, copy, context: dict[str, Any]) -> None:
        raise NotImplementedError

    def revert(self, working, version) -> None:
        raise NotImplementedError

    def check(self, model) -> list[tuple[str, str]]:
        """`(check id, message)` for each way this member fails to name something `model` has."""
        forward, accessors = model_names(model)
        if self.name not in forward and self.name not in accessors:
            return [("E001", f"{self.name} is not a field or related accessor of {model._meta.label}")]
        return []

    def _require(self, context: dict[str, Any]) -> None:
        missing = [key for key in self.reads if key not in context]
        if missing:
            raise VersionSpecError(f"{self.name} reads {missing} but no earlier member provided it")

    def _provide(self, context: dict[str, Any], value) -> None:
        if self.provides:
            context[self.provides] = value


class Reference(Member):
    """A forward FK. An `owned` target belongs to this record: a copy copies it and a revert resets it in
    place; a version that holds no owned target leaves the working target as it is."""

    def __init__(
        self, strategy: Strategy, *, owned: bool = False, reads: tuple[str, ...] = (), provides: str | None = None
    ):
        super().__init__(reads=reads, provides=provides)
        self.strategy = Strategy(strategy)
        self.owned = owned

    def publish(self, working, version, context: dict[str, Any]) -> None:
        self._require(context)
        target = getattr(working, self.name)
        travelled = None if target is None else self._travel(target)
        setattr(version, self.name, travelled)
        self._provide(context, travelled)

    def _travel(self, target):
        match self.strategy:
            case Strategy.NEW_VERSION:
                return target.create_new_version()
            case Strategy.REUSE_UNCHANGED:
                return reuse_or_version(target)
            case Strategy.LIVE_REFERENCE:
                return target
            case Strategy.PRIVATE_CLONE:
                return target.create_new_version(is_copy=True)

    def check(self, model) -> list[tuple[str, str]]:
        field = model_names(model)[0].get(self.name)
        if field is None or not (field.many_to_one or field.one_to_one):
            return [("E001", f"{self.name} is a Reference but not a foreign key of {model._meta.label}")]
        return []

    def copy(self, working, copy, context: dict[str, Any]) -> None:
        self._require(context)
        target = getattr(working, self.name)
        if target is not None and (self.owned or self.strategy is Strategy.PRIVATE_CLONE):
            target = target.create_new_version(is_copy=True)
        setattr(copy, self.name, target)
        self._provide(context, target)

    def revert(self, working, version) -> None:
        if self.strategy is Strategy.LIVE_REFERENCE:
            return
        target = getattr(version, self.name)
        if self.owned:
            if target is None:
                return
            current = getattr(working, self.name)
            if current is None:
                raise ValueError(f"Cannot revert {self.name}: the working record has none")
            current.revert_to_version(target)
            return
        if self.strategy is Strategy.PRIVATE_CLONE:
            setattr(working, self.name, None if target is None else target.create_new_version(is_copy=True))
            return
        setattr(working, self.name, None if target is None else target.get_working_version())


class OwnedSet(Member):
    """Rows reached through reverse accessors that belong to this record.

    `via` is a bridge `(working, other, operation, context)` that carries the set until the rows' own
    model has a spec; `accessors` names the reverse relations it covers, the member's own name by
    default."""

    def __init__(
        self,
        *,
        via: Callable[[Any, Any, Operation, dict[str, Any]], Any],
        accessors: tuple[str, ...] | None = None,
        reads: tuple[str, ...] = (),
        provides: str | None = None,
    ):
        super().__init__(reads=reads, provides=provides)
        self.via = via
        self._accessors = accessors

    @property
    def accessors(self) -> tuple[str, ...]:
        return self._accessors or (self.name,)

    def publish(self, working, version, context: dict[str, Any]) -> None:
        self._require(context)
        self._provide(context, self.via(working, version, Operation.PUBLISH, context))

    def copy(self, working, copy, context: dict[str, Any]) -> None:
        self._require(context)
        self._provide(context, self.via(working, copy, Operation.COPY, context))

    def revert(self, working, version) -> None:
        self.via(working, version, Operation.REVERT, {})

    def check(self, model) -> list[tuple[str, str]]:
        accessors = model_names(model)[1]
        return [
            ("E001", f"{self.name} covers {accessor!r}, which is not a related accessor of {model._meta.label}")
            for accessor in self.accessors
            if accessor not in accessors
        ]


class ParamReference(Member):
    """An id, or a list of ids under `many`, stored under `key` in the JSON field `json_field`; the record
    it names travels by `strategy`.

    `fk_field` (the member's own name by default) is the FK or M2M column that mirrors the id, which a
    revert reads the versioned record from. `archiving` says what happens to the referenced record when
    a version holding the reference is archived; `display_name` labels the value in version details."""

    def __init__(
        self,
        key: str,
        model_label: str,
        strategy: Strategy,
        *,
        json_field: str = "params",
        fk_field: str | None = None,
        many: bool = False,
        archiving: Archiving = Archiving.KEEP,
        display_name: str | None = None,
        reads: tuple[str, ...] = (),
        provides: str | None = None,
    ):
        super().__init__(reads=reads, provides=provides)
        self.key = key
        self.model_label = model_label
        self.strategy = Strategy(strategy)
        self.json_field = json_field
        self._fk_field = fk_field
        self.many = many
        self.archiving = Archiving(archiving)
        self._display_name = display_name
        if many and self.strategy is not Strategy.LIVE_REFERENCE:
            raise VersionSpecError("Versioning multi-ID params is not supported; a many param is a live reference")

    @property
    def fk_field(self) -> str:
        return self._fk_field or self.name

    @property
    def display_name(self) -> str:
        return self._display_name or self.name

    @property
    def model(self):
        return apps.get_model(self.model_label)

    def publish(self, working, version, context: dict[str, Any]) -> None:
        self._require(context)
        params = getattr(version, self.json_field)
        self.pin(params)
        self._provide(context, params.get(self.key))

    def copy(self, working, copy, context: dict[str, Any]) -> None:
        self._require(context)
        self._provide(context, getattr(copy, self.json_field).get(self.key))

    def revert(self, working, version) -> None:
        self.map_back(version, getattr(working, self.json_field))

    def pin(self, params: dict) -> None:
        """Rewrite `params[key]` from the working record to the record a version holds, per `strategy`."""
        if self.strategy is Strategy.LIVE_REFERENCE or not params.get(self.key):
            return
        instance = self.model.objects.filter(id=params[self.key]).first()
        if instance is None or instance.is_a_version:
            return
        travelled = reuse_or_version(instance) if self.strategy is Strategy.REUSE_UNCHANGED else self._travel(instance)
        params[self.key] = str(travelled.id)

    def _travel(self, instance):
        if self.strategy is Strategy.PRIVATE_CLONE:
            return instance.create_new_version(is_copy=True)
        return instance.create_new_version()

    def map_back(self, row, params: dict) -> None:
        """Rewrite `params[key]` from the versioned record `row`'s `fk_field` holds back to its working id;
        a reference whose record is gone is cleared."""
        if self.strategy is Strategy.LIVE_REFERENCE:
            return
        instance = getattr(row, self.fk_field)
        if instance is not None:
            params[self.key] = str(instance.get_working_version_id())
        elif params.get(self.key):
            params[self.key] = None

    def archive_referenced(self, params: dict) -> None:
        """Archive the record(s) `params[key]` names, per `archiving`."""
        if self.archiving is Archiving.KEEP:
            return
        for instance_id in self.referenced_ids(params):
            instance = self.model.objects.filter(id=instance_id).first()
            if instance is None:
                versioning_logger.error("Cannot archive %s %s: not found", self.key, instance_id)
                continue
            if self.archiving is Archiving.ARCHIVE_VERSIONS_ONLY and not instance.is_a_version:
                continue
            instance.archive()

    def unarchive_referenced(self, params: dict) -> None:
        """Restore the records `archive_referenced` archived. Only `ARCHIVE` records come back: archiving a
        collection deletes its remote index and files, so a restored versions-only row would have nothing
        behind it."""
        if self.archiving is not Archiving.ARCHIVE:
            return
        for instance in self.model.objects.get_all().filter(id__in=self.referenced_ids(params), is_archived=True):
            instance.unarchive()

    def referenced_ids(self, params: dict) -> list:
        value = params.get(self.key)
        return (list(value) if self.many else [value]) if value else []

    def resolve_for_display(self, value):
        """The model instance(s) `value` names, for version-detail display."""
        if not value:
            return value
        if self.many:
            return list(self.model.objects.filter(id__in=value))
        return self.model.objects.filter(id=value).first()

    def check(self, model) -> list[tuple[str, str]]:
        forward = model_names(model)[0]
        label = model._meta.label
        findings = []
        if self.json_field not in forward:
            findings.append(("E001", f"{self.name} reads {self.json_field!r}, which is not a field of {label}"))
        mirror = forward.get(self.fk_field)
        if mirror is None or not (mirror.many_to_one or mirror.many_to_many):
            findings.append(("E001", f"{self.name} mirrors {self.fk_field!r}, which is not a relation of {label}"))
        try:
            target = self.model
        except (LookupError, ValueError):
            findings.append(("E004", f"{self.name} names {self.model_label!r}, which does not resolve to a model"))
        else:
            if not issubclass(target, VersionsMixin):
                findings.append(("E001", f"{self.name} names {self.model_label}, which is not a versioned model"))
        return findings


class VersionSpec:
    """Declares how a `VersionsMixin` model travels between its working row and its versions.

    `Meta.model` is an `"app_label.Model"` label, resolved lazily. `Meta.identity` names the fields
    that never travel: the row's own identity, the family columns and lifecycle state. Every other
    concrete field is a row field that travels by being cloned, unless a member declares it.
    `Meta.regenerated` maps an identity field to a factory for the clone's fresh value, applied
    before its first save. Members run in class-body order; the hooks run after them."""

    registry: ClassVar[dict[str, type["VersionSpec"]]] = {}
    members: ClassVar[tuple[Member, ...]] = ()

    class Meta:
        model: str
        identity: frozenset[str] = frozenset()
        regenerated: dict[str, Callable[[], Any]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        label = getattr(cls.Meta, "model", None)
        if not label:
            raise VersionSpecError(f"{cls.__name__}.Meta.model must name the model it binds")
        if label.lower() in cls.registry:
            raise VersionSpecError(f"{label} already has a VersionSpec: {cls.registry[label.lower()].__name__}")
        cls.members = tuple(value for value in cls.__dict__.values() if isinstance(value, Member))
        cls.registry[label.lower()] = cls

    @classmethod
    def for_model(cls, model_cls) -> "VersionSpec | None":
        spec_cls = cls.registry.get(model_cls._meta.label_lower)
        return spec_cls() if spec_cls else None

    @property
    def model(self):
        return apps.get_model(self.Meta.model)

    @property
    def identity(self) -> frozenset[str]:
        return frozenset(getattr(self.Meta, "identity", frozenset()))

    @property
    def regenerated(self) -> dict[str, Callable[[], Any]]:
        return dict(getattr(self.Meta, "regenerated", {}))

    @property
    def row_fields(self) -> list[Field]:
        """The concrete fields that travel by being cloned: not identity, not declared by a member."""
        declared = {member.name for member in self.members}
        return [
            field
            for field in self.model._meta.concrete_fields
            if field.name not in self.identity and field.name not in declared
        ]

    @property
    def travelling_fields(self) -> frozenset[str]:
        """Every field a version carries: the row fields and the declared references."""
        references = {member.name for member in self.members if isinstance(member, Reference)}
        return frozenset({field.name for field in self.row_fields} | references)

    @property
    def numbered(self) -> bool:
        return _has_field(self.model, "version_number")

    @classmethod
    def param_references(cls) -> tuple["ParamReference", ...]:
        return tuple(member for member in cls.members if isinstance(member, ParamReference))

    def on_clone(self, working, clone, **options) -> None:
        """Model-specific work on the clone before its first save: its parent, a renamed key."""

    def on_publish(self, working, version, **options) -> None:
        """Model-specific work after the members, before the version's final save."""

    def on_copy(self, working, copy, **options) -> None:
        """Model-specific work after the members, before the copy's final save."""

    def on_revert(self, working, version) -> None:
        """Model-specific work after the working row and its members are written."""

    @transaction.atomic()
    def publish(self, working, **options):
        """Snapshot `working` into a new version, numbering it and advancing the working row."""
        version = self._clone(working)
        version.working_version_id = working.pk
        if self.numbered:
            version.version_number = self._next_number(working)
        self._regenerate(version)
        self.on_clone(working, version, **options)
        version.save()
        context: dict[str, Any] = {}
        for member in self.members:
            member.publish(working, version, context)
        self.on_publish(working, version, **options)
        version.save()
        return version

    @transaction.atomic()
    def copy(self, working, **options):
        """Duplicate `working` into a new working record with no family link, numbered from one."""
        copy = self._clone(working)
        copy.working_version_id = None
        if self.numbered:
            copy.version_number = 1
        self._regenerate(copy)
        self.on_clone(working, copy, **options)
        copy.save()
        context: dict[str, Any] = {}
        for member in self.members:
            member.copy(working, copy, context)
        self.on_copy(working, copy, **options)
        copy.save()
        return copy

    @transaction.atomic()
    def revert(self, working, version) -> None:
        """Reset `working` to the content of `version`, leaving every version row as it is."""
        name = self.model._meta.verbose_name
        if not working.is_working_version:
            raise ValueError(f"Can only revert the working version of this {name}")
        if version.get_working_version_id() != working.pk:
            raise ValueError(f"Can only revert to a version of this {name}")
        for field in self.row_fields:
            setattr(working, field.attname, getattr(version, field.attname))
        for member in self.members:
            member.revert(working, version)
        working.save()
        self.on_revert(working, version)

    def _next_number(self, working) -> int:
        """The working row's current number, taken under a row lock so two publishers never share
        one; the row is advanced and `working` brought in step with it."""
        locked = self.model.objects.select_for_update().get(pk=working.pk)
        number = locked.version_number
        locked.version_number = number + 1
        locked.save(update_fields=["version_number"])
        working.version_number = locked.version_number
        return number

    def _clone(self, working):
        clone = self.model.objects.get(pk=working.pk)
        clone.pk = None
        clone.id = None
        clone._state.adding = True
        return clone

    def _regenerate(self, clone) -> None:
        for field_name, make in self.regenerated.items():
            setattr(clone, field_name, make())


def _has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
    except FieldDoesNotExist:
        return False
    return True


class VersionsMixin:
    """A model mixin that needs to be added to the model that will be versioned"""

    DEFAULT_EXCLUDED_KEYS = ["id", "created_at", "updated_at", "working_version", "versions", "version_number"]

    # Whether ``get_absolute_url`` on a version snapshot lands on that specific version.
    # Most versioned models reverse to their own id, which has no page of its own, so UI
    # that wants to link to an individual version has to check this before doing so.
    has_version_specific_url = False

    @transaction.atomic()
    def create_new_version(self, save=True, is_copy=False):
        """Publish a version of this record, or copy it when `is_copy`.

        A model with a bound `VersionSpec` is traversed by it. Without one the row is cloned and, when the
        model has a `working_version`, linked to this record; `save` applies to that path only."""
        spec = VersionSpec.for_model(type(self))
        if spec is not None:
            return spec.copy(self) if is_copy else spec.publish(self)
        working_version_id = self.id
        new_instance = self._meta.model.objects.get(id=working_version_id)
        new_instance.pk = None
        new_instance.id = None
        new_instance._state.adding = True
        if hasattr(new_instance, "working_version_id") and not is_copy:
            new_instance.working_version_id = working_version_id

        if save:
            new_instance.save()
        return new_instance

    def revert_to_version(self, version: Self) -> None:
        """Reset this working record to the content of `version`, as its `VersionSpec` declares."""
        spec = VersionSpec.for_model(type(self))
        if spec is None:
            raise NotImplementedError(f"{type(self).__name__} has no VersionSpec to revert by")
        spec.revert(self, version)

    @property
    def is_a_version(self):
        """Return whether or not this experiment is a version of an experiment"""
        return self.working_version_id is not None

    @property
    def is_working_version(self):
        return self.working_version_id is None

    @property
    def latest_version(self):
        return self.versions.order_by("-created_at").first()

    def get_working_version(self) -> Self:
        """Returns the working version of this experiment family"""
        if self.is_working_version:
            return self
        return self.working_version

    def get_working_version_id(self) -> int:
        return self.working_version_id if self.working_version_id else self.id

    @property
    def has_versions(self):
        return self.versions.exists()

    @cached_property
    def version_family_ids(self) -> list[int]:
        """Returns the ids of records in this version family, including the working version"""
        working_version = self.get_working_version()
        version_family_ids = [working_version.id]
        version_family_ids.extend(working_version.versions.values_list("id", flat=True))
        return version_family_ids

    def get_fields_to_exclude(self):
        """Returns a list of fields that should be excluded when comparing two versions."""
        return self.DEFAULT_EXCLUDED_KEYS

    def archive(self) -> bool:
        """Archive this record. Returns False when the archive is refused."""
        self.is_archived = True
        self.save(update_fields=["is_archived"])
        return True

    def unarchive(self):
        self.is_archived = False
        self.save(update_fields=["is_archived"])

    @property
    def is_editable(self) -> bool:
        return not self.is_archived

    def get_version_name(self):
        """Returns version name in form of v + version number, or unreleased if working version."""
        if self.is_working_version:
            return "unreleased"
        return f"v{self.version_number}"

    def compare_with_latest(self) -> bool:
        """
        Returns a boolean if the versioned object differs from its lastest version
        """
        version = self.version_details
        if prev_version := self.latest_version:
            version.compare(prev_version.version_details, early_abort=True)
        return version.fields_changed

    @property
    def version_details(self) -> VersionDetails:
        return self._get_version_details()

    def _get_version_details(self) -> VersionDetails:
        raise NotImplementedError()


class VersionsObjectManagerMixin:
    """A manager mixin that needs to be added to the model manager that will be versioned"""

    def get_all(self):
        """Every row, archived or not, scoped to the parent row when called on a related manager."""
        queryset = super().get_queryset()
        # Django's related manager scopes to the parent in its get_queryset(), which this bypasses to
        # drop the archived filter, so the scoping is applied here. Every Django related manager sets
        # core_filters; calling _apply_rel_filters directly raises if Django renames it, rather than
        # returning rows of every parent.
        if hasattr(self, "core_filters"):
            queryset = self._apply_rel_filters(queryset)
        return queryset

    def get_queryset(self):
        query = (
            super()
            .get_queryset()
            .annotate(
                is_version=Case(
                    When(working_version_id__isnull=False, then=True),
                    When(working_version_id__isnull=True, then=False),
                    output_field=BooleanField(),
                )
            )
        )
        try:
            self.model._meta.get_field("is_archived")
        except FieldDoesNotExist:
            pass
        else:
            query = query.filter(is_archived=False)
        return query

    def working_versions_queryset(self):
        """Returns a queryset with only working versions"""
        return self.get_queryset().filter(working_version=None)

    def all_versions_queryset(self, working_version_id: int):
        queryset = self.get_queryset().filter(Q(working_version_id=working_version_id) | Q(id=working_version_id))
        if hasattr(self.model, "version_number"):
            queryset = queryset.order_by("-version_number")
        return queryset
