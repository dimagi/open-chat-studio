"""The dashboard's filtered activity querysets, moved here from
apps/dashboard/services.py (#3905) so the filter logic has one home. These
reproduce the dashboard's CURRENT semantics - half-open [start_date, end_date)
window, sessions = a human or AI message in the window, SETUP excluded,
message totals include SYSTEM, evaluation activity excluded - which differ
from the v2 usage API semantics in metrics.py. The definition-switch PR
converges the two; until then both live here side by side.

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

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone

from apps.annotations.models import CustomTaggedItem
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import Experiment, ExperimentSession, Participant, SessionStatus
from apps.teams.models import Team

from .filters import CONVERSATION_MESSAGE_TYPES, chat_tag_exists_pair


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
    messages = ChatMessage.objects.filter(chat__team=team, **base_filters).exclude(
        chat__experiment_session__platform=ChannelPlatform.EVALUATIONS
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
        chat_content_type = ContentType.objects.get_for_model(Chat)
        message_content_type = ContentType.objects.get_for_model(ChatMessage)

        # Sessions: chat or any message in it carries the tag (both the link row and its tag
        # must belong to the reading team)
        tag_on_chat, tag_on_msg = chat_tag_exists_pair(team, tag_ids, "chat_id")
        sessions = sessions.annotate(_tchat=tag_on_chat, _tmsg=tag_on_msg).filter(Q(_tchat=True) | Q(_tmsg=True))

        # Experiments: any session's chat or messages carry the tag (both the link row and
        # its tag must belong to the reading team)
        exp_tag_on_chat = Exists(
            CustomTaggedItem.objects.filter(
                team_id=team.id,
                tag__team_id=team.id,
                content_type=chat_content_type,
                object_id__in=Subquery(
                    Chat.objects.filter(experiment_session__experiment=OuterRef(OuterRef("id"))).values("id")
                ),
                tag_id__in=tag_ids,
            )
        )
        exp_tag_on_msg = Exists(
            CustomTaggedItem.objects.filter(
                team_id=team.id,
                tag__team_id=team.id,
                content_type=message_content_type,
                object_id__in=Subquery(
                    ChatMessage.objects.filter(chat__experiment_session__experiment=OuterRef(OuterRef("id"))).values(
                        "id"
                    )
                ),
                tag_id__in=tag_ids,
            )
        )
        experiments = experiments.annotate(_exp_tchat=exp_tag_on_chat, _exp_tmsg=exp_tag_on_msg).filter(
            Q(_exp_tchat=True) | Q(_exp_tmsg=True)
        )

        # Participants: any of their sessions' chats or messages carry the tag (both the
        # link row and its tag must belong to the reading team)
        part_tag_on_chat = Exists(
            CustomTaggedItem.objects.filter(
                team_id=team.id,
                tag__team_id=team.id,
                content_type=chat_content_type,
                object_id__in=Subquery(
                    Chat.objects.filter(experiment_session__participant=OuterRef(OuterRef("id"))).values("id")
                ),
                tag_id__in=tag_ids,
            )
        )
        part_tag_on_msg = Exists(
            CustomTaggedItem.objects.filter(
                team_id=team.id,
                tag__team_id=team.id,
                content_type=message_content_type,
                object_id__in=Subquery(
                    ChatMessage.objects.filter(chat__experiment_session__participant=OuterRef(OuterRef("id"))).values(
                        "id"
                    )
                ),
                tag_id__in=tag_ids,
            )
        )
        participants = participants.annotate(_part_tchat=part_tag_on_chat, _part_tmsg=part_tag_on_msg).filter(
            Q(_part_tchat=True) | Q(_part_tmsg=True)
        )

        # Messages: the message's own tags carry the tag (both the link row and its tag must
        # belong to the reading team). Message-only match - a tag on the chat (rather than the
        # message itself) does not pull the chat's messages in here; that broader chat-or-message
        # match is what the sessions/experiments/participants legs above use via
        # `chat_tag_exists_pair`, not this one.
        msg_tag_on_msg = Exists(
            CustomTaggedItem.objects.filter(
                team_id=team.id,
                tag__team_id=team.id,
                content_type=message_content_type,
                object_id=OuterRef("id"),
                tag_id__in=tag_ids,
            )
        )
        messages = messages.filter(msg_tag_on_msg)

    return {
        "experiments": experiments,
        "sessions": sessions,
        "messages": messages,
        "participants": participants,
        "start_date": start_date,
        "end_date": end_date,
    }
