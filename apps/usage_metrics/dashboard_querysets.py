"""The dashboard's filtered activity querysets, moved here from
apps/dashboard/services.py (#3905) so the filter logic has one home. These
reproduce the dashboard's CURRENT semantics - closed [start_date, end_date]
window, sessions = any message in window, message totals include SYSTEM,
evaluation activity excluded - which differ from the v2 usage API semantics in
metrics.py. The definition-switch PR converges the two; until then both live
here side by side.
"""

from datetime import datetime, timedelta
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone

from apps.annotations.models import CustomTaggedItem
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.models import Chat, ChatMessage
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.teams.models import Team

from .filters import chat_tag_exists_pair


def filtered_querysets(
    team: Team,
    *,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    experiment_ids: list[int] | None = None,
    platform_names: list[str] | None = None,
    participant_ids: list[int] | None = None,
    tag_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Base querysets with the dashboard's common filters applied. Returns
    `experiments`, `sessions`, `messages`, `participants` querysets plus the
    resolved `start_date`/`end_date` (defaulting to the last 30 days)."""

    if not end_date:
        end_date = timezone.now()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    base_filters = {"created_at__gte": start_date, "created_at__lte": end_date}

    experiments = Experiment.objects.filter(team=team, is_archived=False, working_version=None)
    # Use Exists() to avoid join+distinct - prevents row explosion upfront for better performance
    msg_exists = Exists(
        ChatMessage.objects.filter(
            chat=OuterRef("chat"),
            created_at__gte=start_date,
            created_at__lte=end_date,
        )
    )
    sessions = (
        ExperimentSession.objects.filter(team=team)
        .exclude(experiment_channel__platform=ChannelPlatform.EVALUATIONS)
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

        # Sessions: chat or any message in it carries the tag (team's own links only)
        tag_on_chat, tag_on_msg = chat_tag_exists_pair(team, tag_ids, "chat_id")
        sessions = sessions.annotate(_tchat=tag_on_chat, _tmsg=tag_on_msg).filter(Q(_tchat=True) | Q(_tmsg=True))

        # Experiments: any session's chat or messages carry the tag
        exp_tag_on_chat = Exists(
            CustomTaggedItem.objects.filter(
                team_id=team.id,
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

        # Participants: any of their sessions' chats or messages carry the tag
        part_tag_on_chat = Exists(
            CustomTaggedItem.objects.filter(
                team_id=team.id,
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

        # Messages can still use the simple filter since we're already on the message model
        messages = messages.filter(tags__id__in=tag_ids)

    return {
        "experiments": experiments,
        "sessions": sessions,
        "messages": messages,
        "participants": participants,
        "start_date": start_date,
        "end_date": end_date,
    }
