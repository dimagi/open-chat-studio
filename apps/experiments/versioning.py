from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as data_field
from difflib import Differ
from typing import TYPE_CHECKING, Any

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
    group_name: str = data_field(default="")
    previous_field_version: "VersionField" = data_field(default=None)
    changed: bool = False
    label: str = data_field(default="")
    queryset: QuerySet | None = None
    queryset_results: list["VersionField"] = data_field(default_factory=list)
    text_diffs: list[TextDiff] = data_field(default_factory=list)

    def __post_init__(self):
        self.label = self.name.replace("_", " ").title()
        if self.raw_value and not hasattr(self.raw_value, "version"):
            return

        if self.queryset:
            for record in self.queryset.all():
                self.queryset_results.append(VersionField(raw_value=record, to_display=self.to_display))

    def display_value(self) -> Any:
        if self.queryset:
            return self.queryset_results
        if self.to_display:
            return self.to_display(self.raw_value)
        return self.raw_value or ""

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
                self.changed = self.raw_value.id != self.previous_field_version.raw_value.id
            case "versioned_model":
                original = self.raw_value
                new = self.previous_field_version.raw_value
                exclude_model_fields = self.raw_value.get_fields_to_exclude()
                changed_fields = original.compare_with_model(new, exclude_model_fields, early_abort=early_abort)
                self.changed = bool(changed_fields)
            case "queryset":
                self._compare_querysets(early_abort)
            case "primitive":
                self.changed = self.raw_value != self.previous_field_version.raw_value
                if isinstance(self.raw_value, str):
                    self._compute_character_level_diff()

    def _get_field_type(self):
        if isinstance(self.raw_value, VersioningMixin):
            if hasattr(self.raw_value, "working_version"):
                return "versioned_model"
            else:
                return "unversioned_model"
        elif isinstance(self.raw_value, QuerySet):
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
        previous_records_not_used = list(previous_queryset.values_list("id", flat=True))
        for version_field in self.queryset_results:
            record = version_field.raw_value
            previous_record = previous_queryset.filter(id__in=record.version_family_ids).first()

            if previous_record:
                # A version of the current record exists in the previous queryset
                # TODO: When comparing static trigger versions and only the action changed, it is not being picked up.
                previous_records_not_used.remove(previous_record.id)
                prev_version_field = VersionField(raw_value=previous_record, to_display=self.to_display)
                version_field.compare(prev_version_field, early_abort=early_abort)
                self.changed = self.changed or version_field.changed
            else:
                # No version of the current record exists in the previous queryset, thus it is new
                version_field.changed = self.changed = True

            if early_abort and self.changed:
                return

        records_removed_queryset = previous_queryset.filter(id__in=previous_records_not_used)
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
        difflines = list(differ.compare(self.previous_field_version.raw_value, self.raw_value))

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
class Version:
    instance: Any
    fields: list[VersionField]
    fields_changed: bool = False
    previous_instance: Any = data_field(default=None)
    _fields_dict: dict = data_field(default_factory=dict)

    def __post_init__(self):
        for version_field in self.fields:
            self._fields_dict[version_field.name] = version_field

    def get_field(self, field_name: str) -> VersionField:
        return self._fields_dict[field_name]

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

    def compare(self, previous_version_details: "Version", early_abort: bool = False):
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
            field.compare(previous_field_version, early_abort=early_abort)
            self.fields_changed = self.fields_changed or field.changed
            if field.changed and early_abort:
                return
