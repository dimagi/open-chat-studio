from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from apps.experiments.models import Experiment


@dataclass
class VersionField:
    """Represents a specific detail about an experiment. The label is the user friendly name"""

    experiment: "Experiment"
    label: str
    raw_value: any
    to_display: callable = None

    @property
    def display_value(self) -> any:
        if self.to_display:
            return self.to_display(self.raw_value)
        return self.raw_value or ""


class ExperimentVersionFieldDetail(TypedDict):
    current_version: VersionField
    changed: bool = False
    previous_version: VersionField | None = None
