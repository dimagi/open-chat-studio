"""Resolving which sessions a dataset job should pull in.

Deliberately request-free: the same resolution has to happen both in a view (to count or
paginate what the user is looking at) and later inside a Celery task (to walk the sessions
for real, from a stored filter rather than a posted list of ids).
"""

from django.db.models import QuerySet

from apps.evaluations.models import EvaluationMessage
from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.teams.models import Team
from apps.web.dynamic_filters.datastructures import FilterParams


def resolve_dataset_available_sessions(
    team: Team,
    filter_params: FilterParams | None = None,
    dataset_id: int | None = None,
    timezone: str | None = None,
) -> QuerySet[ExperimentSession]:
    """Return the team's sessions matching *filter_params*, minus any already in *dataset_id*.

    Passing ``dataset_id=None`` skips the exclusion, which is what the create flow wants —
    the dataset does not exist yet.
    """
    queryset = ExperimentSession.objects.filter(team=team)
    if filter_params is not None:
        queryset = ExperimentSessionFilter().apply(queryset, filter_params=filter_params, timezone=timezone)
    if dataset_id is not None:
        existing_session_ids = EvaluationMessage.objects.filter(
            evaluationdataset=dataset_id,
            evaluationdataset__team=team,
            session__isnull=False,
        ).values_list("session_id", flat=True)
        queryset = queryset.exclude(id__in=existing_session_ids)
    return queryset
