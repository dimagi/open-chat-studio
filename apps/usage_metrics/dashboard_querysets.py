"""The dashboard's filtered activity querysets, moved here from
apps/dashboard/services.py (#3905) so the filter logic has one home.

These are the canonical definitions per ADR-0051, the same ones metrics.py
computes for the v2 usage API: a half-open [start_date, end_date) window,
`sessions` = a session with a human or AI message in the window, and
evaluation-harness and SETUP-session activity excluded from both the session
and the message querysets.

The `messages` queryset deliberately keeps every message *type*, SYSTEM
included, because `get_tag_analytics_data` reads it to find tagged messages
and a SYSTEM message can carry a tag. Narrowing to conversation turns is the
job of the metrics that count them - see `conversation_messages` in filters.py.

Evaluation-harness activity is decided on ``ExperimentSession.platform``
everywhere in this app. ``ExperimentChannel.platform`` is a separate nullable
column and the two can disagree on a row (ADR-0051).

One deliberate exception: tag-link matching here is narrower than the
dashboard's original (unscoped) match. Every `CustomTaggedItem` lookup in this
module is constrained to the reading team's own rows (`team_id` and
`tag__team_id` both equal to `team.id`), so a link recorded under another team
never qualifies a chat, message, session, experiment, or participant - see the
CodeRabbit finding on PR #4132.
"""

from datetime import datetime, timedelta
from typing import Any

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import ChatMessage
from apps.experiments.models import Experiment, ExperimentSession, Participant, SessionStatus
from apps.teams.models import Team

from .filters import CONVERSATION_MESSAGE_TYPES, chat_tag_exists_pair, tagged_conversation_exists_pair


def filtered_querysets(
    team: Team,
    *,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    experiment_ids: list[int] | None = None,
    platform_names: list[str] | None = None,
    participant_ids: list[int] | None = None,
    tag_ids: list[int] | None = None,
    include_archived: bool = False,
) -> dict[str, Any]:
    """Base querysets with the dashboard's common filters applied. Returns
    `experiments`, `sessions`, `messages`, `participants` querysets plus the
    resolved `start_date`/`end_date` (defaulting to the last 30 days).

    `include_archived` applies to the `experiments` enumeration only. The
    activity querysets count archived-chatbot activity either way (ADR-0051)."""

    if not end_date:
        end_date = timezone.now()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    base_filters = {"created_at__gte": start_date, "created_at__lt": end_date}

    # `Experiment.objects` already filters `is_archived=False` (VersionsObjectManagerMixin);
    # `get_all()` is the archived-inclusive manager.
    experiment_manager = Experiment.objects.get_all() if include_archived else Experiment.objects.all()
    experiments = experiment_manager.filter(team=team, working_version=None)
    # Use Exists() to avoid join+distinct - prevents row explosion upfront for better performance
    msg_exists = Exists(
        ChatMessage.objects.filter(
            chat=OuterRef("chat"),
            message_type__in=CONVERSATION_MESSAGE_TYPES,
            created_at__gte=start_date,
            created_at__lt=end_date,
        )
    )
    sessions = (
        ExperimentSession.objects.filter(team=team)
        .exclude(platform=ChannelPlatform.EVALUATIONS)
        .exclude(status=SessionStatus.SETUP)
        .annotate(_has_msgs=msg_exists)
        .filter(_has_msgs=True)
    )
    messages = (
        ChatMessage.objects.filter(chat__team=team, **base_filters)
        .exclude(chat__experiment_session__platform=ChannelPlatform.EVALUATIONS)
        .exclude(chat__experiment_session__status=SessionStatus.SETUP)
    )
    participants = Participant.objects.filter(team=team).exclude(platform=ChannelPlatform.EVALUATIONS)

    if experiment_ids:
        experiments = experiments.filter(id__in=experiment_ids)
        sessions = sessions.filter(experiment_id__in=experiment_ids)
        messages = messages.filter(chat__experiment_session__experiment_id__in=experiment_ids)
        participants = participants.filter(experimentsession__experiment_id__in=experiment_ids).distinct()

    if platform_names:
        global_platforms = ChannelPlatform.team_global_platforms()
        if not any(p in global_platforms for p in platform_names):
            # only filter experiments if we're filtering by non-global platforms since all experiments
            # will match the global platforms
            experiments = experiments.filter(
                Exists(
                    ExperimentChannel.objects.filter(
                        experiment=OuterRef("pk"),
                        platform__in=platform_names,
                        deleted=False,
                    )
                )
            )
        sessions = sessions.filter(platform__in=platform_names)
        messages = messages.filter(chat__experiment_session__platform__in=platform_names)
        participants = participants.filter(platform__in=platform_names)

    if participant_ids:
        experiments = experiments.filter(sessions__participant__id__in=participant_ids).distinct()
        sessions = sessions.filter(participant__id__in=participant_ids)
        messages = messages.filter(chat__experiment_session__participant__id__in=participant_ids)
        participants = participants.filter(id__in=participant_ids)

    if tag_ids:
        # One tag-match rule for every leg (chat-or-message, team-scoped links):
        # sessions and messages resolve it from their chat id, experiments and
        # participants from their sessions' chats.
        tag_on_chat, tag_on_msg = chat_tag_exists_pair(team, tag_ids, "chat_id")
        sessions = sessions.annotate(_tchat=tag_on_chat, _tmsg=tag_on_msg).filter(Q(_tchat=True) | Q(_tmsg=True))

        exp_tag_on_chat, exp_tag_on_msg = tagged_conversation_exists_pair(
            team, tag_ids, "experiment_session__experiment"
        )
        experiments = experiments.annotate(_exp_tchat=exp_tag_on_chat, _exp_tmsg=exp_tag_on_msg).filter(
            Q(_exp_tchat=True) | Q(_exp_tmsg=True)
        )

        part_tag_on_chat, part_tag_on_msg = tagged_conversation_exists_pair(
            team, tag_ids, "experiment_session__participant"
        )
        participants = participants.annotate(_part_tchat=part_tag_on_chat, _part_tmsg=part_tag_on_msg).filter(
            Q(_part_tchat=True) | Q(_part_tmsg=True)
        )

        # Messages: the same chat-or-message match as every other leg and as
        # `usage_metrics.messages_queryset`, so a chat-level tag narrows the
        # message counts to the tagged conversations it narrows the session
        # counts to, and the dashboard agrees with the API under the filter.
        msg_tag_on_chat, msg_tag_on_msg = chat_tag_exists_pair(team, tag_ids, "chat_id")
        messages = messages.annotate(_msg_tchat=msg_tag_on_chat, _msg_tmsg=msg_tag_on_msg).filter(
            Q(_msg_tchat=True) | Q(_msg_tmsg=True)
        )

    return {
        "experiments": experiments,
        "sessions": sessions,
        "messages": messages,
        "participants": participants,
        "start_date": start_date,
        "end_date": end_date,
    }
