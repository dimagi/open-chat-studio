"""Helpers for the Add Sessions sub-page that clones sessions into an existing dataset."""

from dataclasses import dataclass

from apps.evaluations.exceptions import SessionSelectionTooLargeError
from apps.evaluations.models import DatasetCreationStatus, EvaluationMode
from apps.evaluations.tasks import (
    create_dataset_from_session_messages_task,
    create_dataset_from_sessions_task,
)
from apps.experiments.models import ExperimentSession

# Ceiling on "all sessions matching the filters" for message-level datasets, which — unlike
# session-level ones — cannot be handed the filter itself: the ids travel in the Celery message,
# and EvaluationMessage.create_from_sessions builds every message for every session in memory
# before any of them are written. Lifting the ceiling means teaching that task to stream from a
# stored filter the way create_dataset_from_sessions_task does — issue #3989.
MESSAGE_MODE_ALL_MATCHING_LIMIT = 1000


def check_message_mode_clone_size(count: int) -> None:
    """Reject a message-level "all matching filters" clone that resolves to too many sessions."""
    if count > MESSAGE_MODE_ALL_MATCHING_LIMIT:
        raise SessionSelectionTooLargeError(
            f"{count} sessions match the current filters. Cloning every match is limited to "
            f"{MESSAGE_MODE_ALL_MATCHING_LIMIT} sessions for message-level datasets — narrow the filters, or "
            "select sessions individually."
        )


@dataclass(frozen=True)
class SessionSelection:
    """What a clone request resolved to: an explicit list of sessions, or a filter to re-resolve.

    ``count`` is what the user is told is being cloned, and is populated either way.
    """

    count: int
    external_ids: list[str] | None = None
    filter_query: str | None = None


def resolve_add_sessions_external_ids(*, mode, post_data, base_qs, team):
    """Resolve the external IDs the POST is asking to clone, for the given mode.

    For 'all_matching' and 'sample', the IDs come from `base_qs` (already team-,
    filter-, and dataset-membership-scoped), so no extra validation is needed.
    For 'selected', the IDs come from the request body and must be validated
    against the team.
    """
    if mode == "all_matching":
        return [str(eid) for eid in base_qs.values_list("external_id", flat=True)]
    if mode == "sample":
        try:
            pct = max(1, min(100, int(post_data.get("sample_percent", "20"))))
        except (ValueError, TypeError):
            pct = 20
        sample_count = max(1, round(base_qs.count() * pct / 100))
        return [str(eid) for eid in base_qs.order_by("?").values_list("external_id", flat=True)[:sample_count]]
    raw_ids = [sid.strip() for sid in post_data.get("session_ids", "").split(",") if sid.strip()]
    return [
        str(eid)
        for eid in ExperimentSession.objects.filter(team=team, external_id__in=raw_ids).values_list(
            "external_id", flat=True
        )
    ]


def resolve_sessions_to_clone(*, dataset, mode, post_data, base_qs, team, filter_query) -> SessionSelection:
    """Decide what the clone job should be given: a list of session ids, or the filter itself.

    Session-mode jobs re-resolve the filter inside the task, so "all matching" never materialises
    the ids here — for a large team that list is tens of thousands of UUIDs, too big to travel
    through a Celery message. Every other mode still resolves to explicit ids: 'selected' is a
    user's hand-picked list, and 'sample' cannot be expressed as a filter (it is a random draw).
    """
    if mode == "all_matching":
        if dataset.evaluation_mode == EvaluationMode.SESSION:
            # "" (no active filters) means "every session in the team", and must stay distinct from
            # None, which is how the task is told to use the id list instead.
            return SessionSelection(count=base_qs.count(), filter_query=filter_query or "")
        # Counted before the ids are read, so an oversized selection is rejected without building
        # the list it is being rejected for.
        check_message_mode_clone_size(base_qs.count())
    external_ids = resolve_add_sessions_external_ids(mode=mode, post_data=post_data, base_qs=base_qs, team=team)
    return SessionSelection(count=len(external_ids), external_ids=external_ids)


def mark_dataset_pending(dataset):
    """Clear any prior error state and mark the dataset as PENDING before dispatching."""
    if dataset.is_failed or dataset.error_message:
        dataset.error_message = ""
    dataset.status = DatasetCreationStatus.PENDING
    dataset.save(update_fields=["status", "error_message"])


def dispatch_clone_task(*, dataset, selection: SessionSelection, message_scope, filter_query, timezone):
    """Dispatch the right Celery clone task based on the dataset's evaluation mode."""
    if dataset.evaluation_mode == EvaluationMode.SESSION:
        return create_dataset_from_sessions_task.delay(
            dataset.id,
            dataset.team_id,
            selection.external_ids,
            selection.filter_query,
            timezone,
        )
    external_ids = selection.external_ids or []
    if message_scope == "filtered":
        session_ids, filtered_session_ids = [], external_ids
    else:
        session_ids, filtered_session_ids = external_ids, []
    return create_dataset_from_session_messages_task.delay(
        dataset.id,
        dataset.team_id,
        session_ids,
        filtered_session_ids,
        filter_query,
        timezone,
    )
