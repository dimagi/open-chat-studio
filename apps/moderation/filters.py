from typing import Any, ClassVar

from django.db.models import Exists, OuterRef, QuerySet
from pydantic import Field

from apps.moderation.models import DeniedParticipant
from apps.web.dynamic_filters.base import ChoiceColumnFilter


def _blocked_options() -> list[str | dict[str, Any]]:
    return [
        {"id": ParticipantBlockedFilter.BLOCKED, "label": "Blocked"},
        {"id": ParticipantBlockedFilter.NOT_BLOCKED, "label": "Not blocked"},
    ]


class ParticipantBlockedFilter(ChoiceColumnFilter):
    """Filter rows (with `team_id` and `participant_column`) by whether the participant is on the team's denylist."""

    BLOCKED: ClassVar[str] = "true"
    NOT_BLOCKED: ClassVar[str] = "false"

    query_param: str = "participant_blocked"
    label: str = "Participant Blocked"
    description: str = "Filter by whether the participant is blocked"
    options: list[str | dict[str, Any]] = Field(default_factory=_blocked_options)
    participant_column: str = Field(default="participant_id", exclude=True)

    def _is_blocked(self) -> Exists:
        return Exists(
            DeniedParticipant.objects.filter(
                participant_id=OuterRef(self.participant_column), team_id=OuterRef("team_id")
            )
        )

    def _matching(self, value) -> Exists | None:
        """The condition for the selected options, or None when they select every row (or none)."""
        selected = set(value) & {self.BLOCKED, self.NOT_BLOCKED}
        if selected == {self.BLOCKED}:
            return self._is_blocked()
        if selected == {self.NOT_BLOCKED}:
            return ~self._is_blocked()
        return None

    def apply_any_of(self, queryset, value, timezone=None) -> QuerySet:
        condition = self._matching(value)
        return queryset if condition is None else queryset.filter(condition)

    def apply_excludes(self, queryset, value, timezone=None) -> QuerySet:
        if {self.BLOCKED, self.NOT_BLOCKED} <= set(value):
            return queryset.none()
        condition = self._matching(value)
        return queryset if condition is None else queryset.exclude(condition)

    def apply_all_of(self, queryset, value, timezone=None) -> QuerySet:
        """The filter does not offer "all of", so a query parameter that uses it has no effect."""
        return queryset
