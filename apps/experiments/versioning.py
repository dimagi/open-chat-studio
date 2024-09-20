from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from apps.experiments.helpers import differs

if TYPE_CHECKING:
    from apps.experiments.models import Experiment


@dataclass
class VersionField:
    """Represents a specific detail about an experiment. The label is the user friendly name"""

    name: str
    raw_value: any
    group_name: str
    to_display: callable = None
    previous_field_version: "VersionField" = field(default=None)
    changed: bool = False
    label: str = field(default="")

    def __post_init__(self):
        self.label = self.name.replace("_", " ").title()

    def display_value(self) -> any:
        if self.to_display:
            return self.to_display(self.raw_value)
        return self.raw_value or ""


@dataclass
class VersionDetails:
    # TODO: Test
    experiment: "Experiment"
    fields: list[VersionField]
    fields_changed: bool = False
    previous_experiment: "Experiment" = field(default=None)

    def get_field(self, field_name: str) -> VersionField:
        # TODO: Use dict to make it faster
        for version_field in self.fields:
            if version_field.name == field_name:
                return version_field

    @property
    def fields_grouped(self):
        # TODO: Return fields with group info for display purposes
        pass

    def compare(self, previous_version_details: "VersionDetails") -> list[str]:
        """Returns a list of fields that changed between this experiment and `target_experiment`"""
        self.previous_experiment = previous_version_details.experiment
        for version_field in self.fields:
            current_value = version_field.raw_value
            previous_field_version = previous_version_details.get_field(version_field.name)
            prev_version_raw_value = previous_field_version.raw_value
            version_field.previous_field_version = previous_field_version
            if differs(
                current_value, prev_version_raw_value, exclude_model_fields=self.experiment.DEFAULT_EXCLUDED_KEYS
            ):
                self.fields_changed = version_field.changed = True
