from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as data_field
from difflib import Differ
from typing import TYPE_CHECKING, Any, Self

from django.db.models import QuerySet

from apps.utils.models import VersioningMixin

if TYPE_CHECKING:
    pass


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
class TextDiff:
    character: str
    added: bool = False
    removed: bool = False


@dataclass
class VersionField:
    """Represents a specific detail about the instance. The label is the user friendly name"""

    name: str = ""
    raw_value: Any | None = None
    to_display: callable = None
    group_name: str = data_field(default="General")
    previous_field_version: "VersionField" = data_field(default=None)
    changed: bool = False
    label: str = data_field(default="")
    queryset: QuerySet | None = None
    queryset_results: list["VersionField"] = data_field(default_factory=list)
    text_diffs: list[TextDiff] = data_field(default_factory=list)
    raw_value_version: Self | None = None

    @property
    def current_value(self):
        return self.raw_value

    @property
    def previous_value(self):
        return self.previous_field_version.raw_value

    @property
    def is_a_version(self):
        return self.raw_value_version is not None

    def __post_init__(self):
        self.label = self.name.replace("_", " ").title()
        if self.current_value and not hasattr(self.current_value, "version_details"):
            return

        if self.queryset:
            for record in self.queryset.all():
                self.queryset_results.append(VersionField(raw_value=record, to_display=self.to_display))

    def display_value(self) -> Any:
        to_display = self.to_display or default_to_display
        if self.queryset:
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

        if not previous_field_version:
            previous_field_version = VersionField(
                name=self.name,
                to_display=self.to_display,
                group_name=self.group_name,
            )
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
                if isinstance(self.current_value, str):
                    self._compute_character_level_diff()

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
        previous_queryset = self.previous_field_version.queryset
        previous_records = list(previous_queryset.values_list("id", flat=True))
        for version_field in self.queryset_results:
            record = version_field.raw_value
            previous_record = previous_queryset.filter(id__in=record.version_family_ids).first()

            if previous_record:
                # A version of the current record exists in the previous queryset
                previous_records.remove(previous_record.id)
                prev_version_field = VersionField(raw_value=previous_record, to_display=self.to_display)
                version_field.compare(prev_version_field, early_abort=early_abort)
                self.changed = self.changed or version_field.changed
            else:
                # No version of the current record exists in the previous queryset, thus it is new
                version_field.changed = self.changed = True

            if early_abort and self.changed:
                return

        records_removed_queryset = previous_queryset.filter(id__in=previous_records)
        if records_removed_queryset.exists():
            self.changed = True
            if early_abort:
                return

        for record in records_removed_queryset.all():
            # We need to add version fields for each removed record, but with the current value set to None
            prev_version_field = VersionField(raw_value=record, to_display=self.to_display)
            version_field = VersionField(raw_value=None, previous_field_version=prev_version_field, changed=True)
            self.queryset_results.append(version_field)

    def _compute_character_level_diff(self):
        differ = Differ()
        difflines = list(differ.compare(self.previous_value or "", self.current_value))

        for line in difflines:
            operation, character = line[0], line[2:]
            match operation:
                case " ":
                    # line is same in both
                    self.previous_field_version.text_diffs.append(TextDiff(character=character))
                    self.text_diffs.append(TextDiff(character=character))
                case "-":
                    # line is only on the left
                    self.previous_field_version.text_diffs.append(TextDiff(character=character, removed=True))
                case "+":
                    # line is only on the right
                    self.text_diffs.append(TextDiff(character=character, added=True))


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

    def get_field(self, field_name: str) -> VersionField:
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

    # def compare(self, previous_version_details: Self, early_abort: bool = False):
    #     """
    #     Compares the current instance with the previous version and updates the changed status of fields.

    #     Args:
    #         previous_version_details (Version): The previous version details to compare against.
    #         early_abort (bool): If True, the comparison will stop as soon as the first difference is found.
    #     """
    #     previous_instance = previous_version_details.instance

    #     if self.instance and previous_instance and type(self.instance) != type(previous_instance):  # noqa: E721
    #         prev_instance_type = type(self.previous_instance)
    #         curr_instance_type = type(self.instance)
    #         raise TypeError(
    #             f"Cannot compare instances of different types: {curr_instance_type} and {prev_instance_type}."
    #         )
    #     self.previous_instance = previous_instance

    #     for field in self.fields:
    #         previous_field_version = previous_version_details.get_field(field.name)
    #         field.compare(previous_field_version, early_abort=early_abort)
    #         self.fields_changed = self.fields_changed or field.changed
    #         if field.changed and early_abort:
    #             return

    #     for previous_field in previous_version_details.fields:
    #         # When a field was totally removed from the new instance, we still need to track an empty value for it
    #         current_field_version = self.get_field(previous_field.name)
    #         if not current_field_version:
    #             missing_field = VersionField(
    #                 name=previous_field.name,
    #                 to_display=previous_field.to_display,
    #                 group_name=previous_field.group_name,
    #             )
    #             self.fields.append(missing_field)
    #             self._fields_dict[missing_field.name] = missing_field
    #             missing_field.compare(previous_field, early_abort=early_abort)


class VersionedField:
    name: str
    group_name: str = data_field(default="General")
    to_display: Callable = None

    def display_name(self):
        return self.name.replace("_", " ").title()


@dataclass
class CharacterDiff:
    current: list[TextDiff]
    previous: list[TextDiff]
    type: str = "character_diff"

    @property
    def changed(self):
        for diff in self.current:
            if diff.added or diff.removed:
                return True

        for diff in self.previous:
            if diff.added or diff.removed:
                return True

        return False


@dataclass
class ValueDiff:
    current: Any
    previous: Any
    type: str = "value_diff"

    @property
    def changed(self):
        return self.current != self.previous


@dataclass
class QuerysetDiff:
    current: Any = None
    previous: Any = None
    diffs: list["InstanceDiff"] = data_field(default_factory=list)
    type: str = "queryset_diff"

    @property
    def changed(self):
        for diff in self.diffs:
            if diff.changed:
                return True

        return False


@dataclass
class InstanceDiff:
    current: Any = None
    previous: Any = None
    diffs: list["FieldDiff"] = data_field(default_factory=list)
    type: str = "instance_diff"

    @property
    def changed(self):
        for diff in self.diffs:
            if diff.changed:
                return True

        return False

    def __post_init__(self) -> Self:
        instance = self.current or self.previous
        for field_name in instance.versioned_fields:
            current_value = self.current.get_versioned_field_value(field_name) if self.current else None
            previous_value = self.previous.get_versioned_field_value(field_name) if self.previous else None
            field_diff = FieldDiff(field_name=field_name, current=current_value, previous=previous_value)
            self.diffs.append(field_diff)


@dataclass
class FieldDiff:
    # maybe store the parent diff here as well for easy traversal if we need to
    field_name: str
    current: Any = None
    previous: Any = None
    diff: QuerysetDiff | InstanceDiff | CharacterDiff | ValueDiff | None = None
    type: str = "field_diff"

    def current_display_value(self):
        if self.field.to_display:
            return self.field.to_display(self.current)
        else:
            return self.field.display_name()

    def previous_display_value(self):
        if self.field.to_display:
            return self.field.to_display(self.previous)
        else:
            return self.field.display_name()

    @property
    def changed(self):
        return self.diff.changed

    def __post_init__(self):
        value = self.current or self.previous
        if hasattr(value, "working_version"):
            self.diff = InstanceDiff(self.current, self.previous)
        elif isinstance(value, QuerySet):
            self.diff = QuerysetDiff(
                current=self.current, previous=self.previous, diffs=self._get_instance_diffs_for_queryset()
            )
        elif isinstance(value, str):
            self.current = self.current or ""
            self.previous = self.previous or ""
            current, previous = self._compute_character_level_diff()
            self.diff = CharacterDiff(current=current, previous=previous)
        else:
            self.diff = ValueDiff(current=self.current, previous=self.previous)

    def _get_instance_diffs_for_queryset(self) -> list[InstanceDiff]:
        """
        We need to compare the instances in the current queryset with the instances in the previous queryset.
        Instances can only be compared when they are versions of each other.
        We need to consider the scenario where we add a new instance or remove a previous instance, in which case
        they cannot be compared.
        """
        if self.current is None:
            self.current = []

        if self.previous:
            instances_not_compared = list(self.previous.values_list("id", flat=True))
        else:
            instances_not_compared = []
            self.previous = []

        generated_instance_diffs = []
        for current_instance in self.current:
            previous_instance = None
            if self.previous:
                previous_instance = self.previous.filter(id__in=current_instance.version_family_ids).first()

            if previous_instance:
                # A version of the current instance exists in the previous queryset
                instances_not_compared.remove(previous_instance.id)
                instance_diff = InstanceDiff.compare(current_instance, previous_instance)
                generated_instance_diffs.append(instance_diff)
            else:
                # This accounts for newly added instances
                instance_diff = InstanceDiff(current=current_instance, previous=None)
                generated_instance_diffs.append(instance_diff)

        if self.previous:
            for instance in self.previous.filter(id__in=instances_not_compared):
                # This accounts for newly removed instances
                instance_diff = InstanceDiff(current=None, previous=instance)
                generated_instance_diffs.append(instance_diff)

        return generated_instance_diffs

    def _compute_character_level_diff(self):
        differ = Differ()
        difflines = list(differ.compare(self.previous, self.current))
        current_diffs = []
        previous_diffs = []

        for line in difflines:
            operation, character = line[0], line[2:]
            match operation:
                case " ":
                    # line is same in both
                    previous_diffs.append(TextDiff(character=character))
                    current_diffs.append(TextDiff(character=character))
                case "-":
                    # line is only on the left
                    previous_diffs.append(TextDiff(character=character, removed=True))
                case "+":
                    # line is only on the right
                    current_diffs.append(TextDiff(character=character, added=True))

        return current_diffs, previous_diffs
