from apps.moderation.models import DeniedParticipant, DeniedParticipantSource
from apps.teams.flags import Flags
from apps.teams.roles import is_member
from apps.teams.utils import flag_is_active_for_team

BLOCKED_MESSAGE = "You have been blocked from using this chatbot."


def block_participant(
    *,
    participant,
    source: DeniedParticipantSource,
    reason: str = "",
    experiment=None,
    chat_message=None,
    created_by=None,
) -> tuple[DeniedParticipant, bool]:
    """Add the participant to its team's denylist. Returns the row and whether it was created."""
    return DeniedParticipant.objects.get_or_create(
        team_id=participant.team_id,
        participant=participant,
        defaults={
            "source": source,
            "reason": reason,
            "experiment": experiment,
            "chat_message": chat_message,
            "created_by": created_by,
        },
    )


def abuse_detection_enabled(team) -> bool:
    return flag_is_active_for_team(team, Flags.ABUSE_DETECTION.slug)


def is_on_denylist(team, participant_id: int | None) -> bool:
    """Whether the participant has a denylist row for the team. Ignores the feature flag."""
    if participant_id is None:
        return False
    return DeniedParticipant.objects.filter(team=team, participant_id=participant_id).exists()


def is_participant_blocked(team, participant) -> bool:
    """Whether the participant is on the team's denylist. Always False while the feature flag is off."""
    if participant is None or participant.pk is None:
        return False
    return abuse_detection_enabled(team) and is_on_denylist(team, participant.pk)


def is_identifier_blocked(team, identifier: str, platform: str) -> bool:
    """`is_participant_blocked` for a participant known only by its normalized identifier and platform."""
    if not abuse_detection_enabled(team):
        return False
    return DeniedParticipant.objects.filter(
        team=team, participant__identifier=identifier, participant__platform=platform
    ).exists()


def is_exempt(participant, team) -> bool:
    """Whether the participant is linked to a user who is currently a member of the team."""
    return participant.user_id is not None and is_member(participant.user, team)
