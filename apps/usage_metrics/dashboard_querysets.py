"""The dashboard's filtered activity querysets: the session and message
universes `metrics.py` defines (ADR-0051), plus the two enumerations only the
dashboard draws, its chatbots and its participants.

Every tag-link lookup here is constrained to the reading team's own rows
(`team_id` and `tag__team_id`), so a link recorded under another team never
qualifies an experiment or participant - see the CodeRabbit finding on PR #4132.
"""

from datetime import datetime
from typing import Any

from django.db.models import Exists, OuterRef, Q, QuerySet

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.models import Experiment, Participant
from apps.teams.models import Team

from .filters import UsageFilters, evaluation_platform, tagged_conversation_exists_pair
from .metrics import messages_queryset, sessions_active_queryset


def filtered_querysets(team: Team, *, start: datetime, end: datetime, filters: UsageFilters) -> dict[str, Any]:
    """`experiments`, `sessions`, `messages` and `participants` querysets for `[start, end)` under `filters`.

    `sessions` and `messages` are `sessions_active_queryset` and `messages_queryset`. `experiments` and
    `participants` enumerate what the team has, archived chatbots only when `filters.include_archived`.
    A platform filter means a channel on that platform for a chatbot and the session's own platform
    column for everything else."""
    experiments = Experiment.objects.get_all() if filters.include_archived else Experiment.objects.all()
    experiments = experiments.filter(team=team, working_version=None)
    participants = Participant.objects.filter(team=team).exclude(evaluation_platform())

    if filters.experiment_ids is not None:
        experiments = experiments.filter(id__in=filters.experiment_ids)
        participants = participants.filter(experimentsession__experiment_id__in=filters.experiment_ids).distinct()

    if filters.platforms is not None:
        # Every chatbot has the team-global platforms, so only a pick of other platforms narrows the list.
        global_platforms = ChannelPlatform.team_global_platforms()
        if not any(platform in global_platforms for platform in filters.platforms):
            experiments = experiments.filter(
                Exists(
                    ExperimentChannel.objects.filter(
                        experiment=OuterRef("pk"), platform__in=filters.platforms, deleted=False
                    )
                )
            )
        participants = participants.filter(platform__in=filters.platforms)

    if filters.participant_ids is not None:
        experiments = experiments.filter(sessions__participant__id__in=filters.participant_ids).distinct()
        participants = participants.filter(id__in=filters.participant_ids)

    if filters.tag_ids:
        experiments = _tagged(experiments, team, filters.tag_ids, "experiment_session__experiment")
        participants = _tagged(participants, team, filters.tag_ids, "experiment_session__participant")

    return {
        "experiments": experiments,
        "sessions": sessions_active_queryset(team, start=start, end=end, filters=filters),
        "messages": messages_queryset(team, start=start, end=end, filters=filters),
        "participants": participants,
    }


def _tagged(queryset: QuerySet, team: Team, tag_ids: list[int], session_path: str) -> QuerySet:
    """Narrow an experiment or participant queryset to the ones with a tagged conversation."""
    tag_on_chat, tag_on_msg = tagged_conversation_exists_pair(team, tag_ids, session_path)
    return queryset.annotate(_tag_on_chat=tag_on_chat, _tag_on_msg=tag_on_msg).filter(
        Q(_tag_on_chat=True) | Q(_tag_on_msg=True)
    )
