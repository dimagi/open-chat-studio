from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as data_field
from typing import TYPE_CHECKING, Any

from django.db.models import Model

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
class VersionField:
    """Represents a specific detail about the instance. The label is the user friendly name"""

    name: str
    raw_value: Any
    group_name: str
    to_display: callable = None
    previous_field_version: "VersionField" = data_field(default=None)
    changed: bool = False
    label: str = data_field(default="")

    def __post_init__(self):
        self.label = self.name.replace("_", " ").title()

    def display_value(self) -> Any:
        if self.to_display:
            return self.to_display(self.raw_value)
        return self.raw_value or ""


@dataclass
class FieldGroup:
    name: str
    fields: list[VersionField] = data_field(default_factory=list)
    show: bool = data_field(default=False)
    # Indicates whether a field in this group changed
    has_changed_fields: bool = data_field(default=False)


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
            group_info.show = group_info.show or bool(field.raw_value) or bool(field.changed)
            group_info.has_changed_fields = group_info.has_changed_fields or field.changed
            group_info.fields.append(field)
        return list(groups.values())

    def compare(self, previous_version_details: "Version"):
        """Compares the current instance with the previous version and updates the changed status of fields."""
        self.previous_instance = previous_version_details.instance

        if type(self.previous_instance) != type(self.instance):  # noqa: E721
            prev_instance_type = type(self.previous_instance)
            curr_instance_type = type(self.instance)
            raise TypeError(
                f"Cannot compare instances of different types: {curr_instance_type} and {prev_instance_type}."
            )

        for version_field in self.fields:
            current_value = version_field.raw_value
            previous_field_version = previous_version_details.get_field(version_field.name)
            version_field.previous_field_version = previous_field_version
            prev_version_raw_value = previous_field_version.raw_value
            if differs(current_value, prev_version_raw_value, exclude_model_fields=self.instance.DEFAULT_EXCLUDED_KEYS):
                self.fields_changed = version_field.changed = True
