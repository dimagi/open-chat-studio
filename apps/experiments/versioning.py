from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as data_field
from typing import TYPE_CHECKING, Any

from django.db.models import Model, QuerySet

if TYPE_CHECKING:
    pass


def differs(original: Any, new: Any, exclude_model_fields: list[str] | None = None) -> bool:
    """
    Compares the value (or attributes in the case of a Model) between `original` and `new`.
    Returns `True` if it differs and `False` if not.

    When comparing models, fields in `exclude_model_fields` will be excluded.
    """
    exclude_model_fields = exclude_model_fields or []
    if isinstance(original, Model) and isinstance(new, Model):
        return bool(compare_models(original, new, exclude_model_fields))
    return original != new


def compare_models(original: Model, new: Model, exclude_fields: list[str]) -> set:
    """
    Compares the field values of between `original` and `new`, excluding those in `exclude_fields`.
    `expected_changed_fields` specifies what fields we expect there to be differences in
    """
    model_fields = [field.attname for field in original._meta.fields]
    original_dict, new_dict = original.__dict__, new.__dict__
    changed_fields = set([])
    for field_name, field_value in original_dict.items():
        if field_name not in model_fields:
            continue

        if field_name in exclude_fields:
            continue
        if field_value != new_dict[field_name]:
            changed_fields.add(field_name)

    return changed_fields


@dataclass
class FieldGroup:
    name: str
    fields: list["VersionField"] = data_field(default_factory=list)
    show: bool = data_field(default=False)
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
    queryset_result_versions: list["VersionField"] = data_field(default_factory=list)
    text_diffs: list[TextDiff] = data_field(default_factory=list)

    def __post_init__(self):
        self.label = self.name.replace("_", " ").title()
        if self.raw_value and not hasattr(self.raw_value, "version"):
            return

        if self.queryset:
            for record in self.queryset.all():
                self.queryset_result_versions.append(VersionField(raw_value=record, to_display=self.to_display))

    @property
    def is_queryset(self) -> bool:
        return bool(self.queryset)

    def display_value(self) -> Any:
        if self.queryset:
            return self.queryset_result_versions
        if self.to_display:
            return self.to_display(self.raw_value)
        return self.raw_value or ""

    def compare(self, previous_field_version: "VersionField", exclude_fields: list):
        self.previous_field_version = previous_field_version
        if self.queryset:
            self._compare_queryset(previous_field_version.queryset)
        else:
            if differs(self.raw_value, previous_field_version.raw_value, exclude_model_fields=exclude_fields):
                self.changed = True
                if isinstance(self.raw_value, str):
                    self._compute_character_level_diff()

    def _compare_queryset(self, previous_queryset):
        """
        Comparing querysets does the following:
        For each item in the current queryset, we need to check if there's a version of it in the previous queryset to
        compare to. If not, it means that the item was added. To detect removed items, we need to get all items from the
        previous queryset that are not versions of the items in the first queryset.
        """
        previous_record_version_ids = []
        for version_field in self.queryset_result_versions:
            record = version_field.raw_value
            working_version = record.get_working_version()
            version_family_ids = [working_version.id]
            version_family_ids.extend(working_version.versions.values_list("id", flat=True))
            previous_record = previous_queryset.filter(id__in=version_family_ids).first()

            if previous_record:
                # TODO: When comparing static trigger versions and only the action changed, it is not being picked up.
                previous_record_version_ids.append(previous_record.id)
                prev_version_field = VersionField(raw_value=previous_record, to_display=self.to_display)
                version_field.compare(prev_version_field, exclude_fields=record.get_fields_to_exclude())
                self.changed = self.changed or version_field.changed
            else:
                version_field.changed = self.changed = True

        for previous_record in previous_queryset.exclude(id__in=previous_record_version_ids):
            # A previous record missing from the current queryset means that something changed
            self.changed = True
            prev_version_field = VersionField(raw_value=previous_record, to_display=self.to_display)
            version_field = VersionField(raw_value=None, previous_field_version=prev_version_field, changed=True)
            self.queryset_result_versions.append(version_field)

    def _compute_character_level_diff(self):
        from difflib import Differ

        differ = Differ()
        difflines = list(differ.compare(self.previous_field_version.raw_value, self.raw_value))
        operations = {
            "no_change": " ",
            "removed": "-",
            "added": "+",
        }

        print(difflines)
        for line in difflines:
            operation = line[0]
            character = line[2:]
            if operation == operations["no_change"]:
                # line is same in both
                self.previous_field_version.text_diffs.append(TextDiff(character=character))
                self.text_diffs.append(TextDiff(character=character))

            elif operation == operations["removed"]:
                # line is only on the left
                self.previous_field_version.text_diffs.append(TextDiff(character=character, removed=True))

            elif operation == operations["added"]:
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
            group_info.show = (
                group_info.show or bool(field.raw_value) or bool(field.changed) or bool(field.queryset_result_versions)
            )
            group_info.has_changed_fields = group_info.has_changed_fields or field.changed
            group_info.fields.append(field)
        return list(groups.values())

    def compare(self, previous_version_details: "Version"):
        """Compares the current instance with the previous version and updates the changed status of fields."""
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
            field.compare(previous_field_version, exclude_fields=self.instance.get_fields_to_exclude())
            self.fields_changed = self.fields_changed or field.changed
