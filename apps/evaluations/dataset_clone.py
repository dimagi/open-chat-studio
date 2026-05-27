"""Helpers for the Add Sessions sub-page that clones sessions into an existing dataset."""

from apps.evaluations.models import DatasetCreationStatus, EvaluationMode
from apps.evaluations.tasks import (
    create_dataset_from_session_messages_task,
    create_dataset_from_sessions_task,
)
from apps.experiments.models import ExperimentSession


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


def mark_dataset_pending(dataset):
    """Clear any prior error state and mark the dataset as PENDING before dispatching."""
    if dataset.is_failed or dataset.error_message:
        dataset.error_message = ""
    dataset.status = DatasetCreationStatus.PENDING
    dataset.save(update_fields=["status", "error_message"])


def dispatch_clone_task(*, dataset, external_ids, message_scope, filter_query, timezone):
    """Dispatch the right Celery clone task based on the dataset's evaluation mode."""
    if dataset.evaluation_mode == EvaluationMode.SESSION:
        return create_dataset_from_sessions_task.delay(dataset.id, dataset.team_id, external_ids)
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
