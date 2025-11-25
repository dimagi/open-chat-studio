from uuid import UUID

from django.db.models import OuterRef, Subquery

from apps.experiments.models import ExperimentSession, Participant, SessionStatus

STATUSES_FOR_COMPLETE_CHATS = [SessionStatus.PENDING_REVIEW, SessionStatus.COMPLETE, SessionStatus.UNKNOWN]


def _get_latest_sessions_for_participants(
    participant_chat_ids: list, experiment_public_id: UUID
) -> list[ExperimentSession]:
    latest_session_id = (
        ExperimentSession.objects.filter(experiment__public_id=experiment_public_id, participant=OuterRef("pk"))
        .order_by("-created_at")
        .values("id")[:1]
    )

    latest_participant_session_ids = (
        Participant.objects.filter(
            experimentsession__experiment__public_id=experiment_public_id, identifier__in=participant_chat_ids
        )
        .annotate(latest_session_id=Subquery(latest_session_id))
        .values("latest_session_id")
    )

    return (
        ExperimentSession.objects.filter(id__in=Subquery(latest_participant_session_ids))
        .prefetch_related("experiment")
        .all()
    )
