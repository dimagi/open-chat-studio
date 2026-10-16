from typing import ClassVar

from django.db.models import Exists, OuterRef
from pydantic import Field

from apps.moderation.models import DeniedParticipant
from apps.web.dynamic_filters.base import YesNoExistsFilter


class ParticipantBlockedFilter(YesNoExistsFilter):
    """Filter on whether the participant is on the team's denylist.

    `participant_column` names the outer row's participant id column; the outer row must have a `team_id`.
    """

    yes_label: ClassVar[str] = "Blocked"
    no_label: ClassVar[str] = "Not blocked"

    query_param: str = "participant_blocked"
    label: str = "Participant Blocked"
    description: str = "Filter by whether the participant is blocked"
    participant_column: str = Field(default="participant_id", exclude=True)

    def _condition(self) -> Exists:
        return Exists(
            DeniedParticipant.objects.filter(
                participant_id=OuterRef(self.participant_column), team_id=OuterRef("team_id")
            )
        )
