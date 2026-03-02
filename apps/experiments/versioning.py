import contextlib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as data_field
from functools import cached_property
from typing import Any, Self

from django.core.exceptions import FieldDoesNotExist
from django.db import transaction
from django.db.models import (
    BooleanField,
    Case,
    Q,
    QuerySet,
    When,
)

from apps.utils.models import VersioningMixin


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
    to_display: Callable | None = None
    group_name: str = data_field(default="General")
    previous_field_version: "VersionField | None" = data_field(default=None)
    changed: bool = False
    label: str = data_field(default="")
    queryset: QuerySet | None = None
    raw_value_version: Self | None = None
    num_results_truncated: int = 0

    @property
    def current_value(self):
        return self.raw_value if self.raw_value else None

    @property
    def previous_value(self):
        return self.previous_field_version.raw_value if self.previous_field_version.raw_value else None

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
        if self.current_value:
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
        previous_queryset_results = self.previous_field_version.queryset_results
        previous_records = {result.raw_value.id: result.raw_value for result in previous_queryset_results}
        for version_field in self.queryset_results:
            record = version_field.raw_value
            previous_record = None
            for version_id in record.version_family_ids:
                if version_id in previous_records:
                    previous_record = previous_records.pop(version_id)
                    break

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

        if previous_records:
            self.changed = True
            if early_abort:
                return

        for record in previous_records.values():
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


class VersionsMixin:
    """A model mixin that needs to be added to the model that will be versioned"""

    DEFAULT_EXCLUDED_KEYS = ["id", "created_at", "updated_at", "working_version", "versions", "version_number"]

    @transaction.atomic()
    def create_new_version(self, save=True, is_copy=False, **kwargs):
        """
        Creates a new version of this instance and sets the `working_version_id` (if this model supports it) to the
        original instance ID
        """
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

    def archive(self):
        self.is_archived = True
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

    @cached_property
    def version_details(self) -> VersionDetails:
        return self._get_version_details()

    def _get_version_details(self) -> VersionDetails:
        raise NotImplementedError()

    def save(self, *args, **kwargs):
        self._clear_version_cache()
        super().save(*args, **kwargs)

    def _clear_version_cache(self):
        with contextlib.suppress(AttributeError):
            del self.version_details


class VersionsObjectManagerMixin:
    """A manager mixin that needs to be added to the model manager that will be versioned"""

    def get_all(self):
        """A method to return all experiments whether it is deprecated or not"""
        return super().get_queryset()

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
