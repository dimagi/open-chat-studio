"""Filter vocabulary for the usage-metrics read path (#3905).

`UsageFilters` is the contract the usage surfaces code against. The predicates
below are the one definition of what activity metrics leave out (ADR-0051):
evaluation-harness rows and sessions still in `SETUP`. Every surface imports
them; none spells the exclusion out again. The tag helpers are this app's
single definition of "this conversation carries one of these tags".
"""

from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery

from apps.annotations.models import CustomTaggedItem
from apps.channels.models import ChannelPlatform
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.teams.models import Team


@dataclass(frozen=True)
class UsageFilters:
    """The filters every usage-metrics read honours, bundled so the metric
    functions take one argument instead of five.

    `experiment_ids`, `participant_ids` and `platforms`: `None` means "no
    filter"; an empty list means "requested but matched nobody" and yields
    empty results. `tag_ids` narrows to conversations whose chat or any message
    in it carries one of the tags; an empty list is treated as no filter.

    `include_archived` applies to experiment enumeration only, which
    `filtered_querysets` returns for the dashboard. Activity metrics count
    archived-chatbot activity regardless (ADR-0051).
    """

    experiment_ids: list[int] | None = None
    participant_ids: list[int] | None = None
    platforms: list[str] | None = None
    tag_ids: list[int] | None = None
    include_archived: bool = True


# The message types that are conversation turns. `system` messages are internal
# bookkeeping and are not counted by any metric (ADR-0051).
CONVERSATION_MESSAGE_TYPES = (ChatMessageType.HUMAN, ChatMessageType.AI)

# What makes a participant active: they authored a HUMAN message. Receiving AI
# output is not activity (ADR-0051). Every surface's participant count filters
# on this one predicate.
HUMAN_AUTHORED = Q(message_type=ChatMessageType.HUMAN)
AI_AUTHORED = Q(message_type=ChatMessageType.AI)


def evaluation_platform(path: str = "") -> Q:
    """Rows whose `platform` column, reached through the lookup prefix `path`, is the evaluation harness."""
    return Q(**{f"{path}platform": ChannelPlatform.EVALUATIONS})


def setup_session(path: str = "") -> Q:
    """Sessions reached through `path` that are still in `SETUP`: created but never engaged."""
    return Q(**{f"{path}status": SessionStatus.SETUP})


def exclude_non_activity(queryset: QuerySet, session_path: str = "") -> QuerySet:
    """Drop evaluation-harness sessions and sessions still in `SETUP`, reached through `session_path` (ADR-0051).

    Each exclusion negates one lookup, which Django guards with `IS NOT NULL`, so a session with no
    platform, or a message whose chat has no session, stays in the universe."""
    return queryset.exclude(evaluation_platform(session_path)).exclude(setup_session(session_path))


def activity_sessions(session_queryset: QuerySet[ExperimentSession]) -> QuerySet[ExperimentSession]:
    """Narrow a session queryset to the sessions activity metrics count (ADR-0051)."""
    return exclude_non_activity(session_queryset)


def activity_messages(message_queryset: QuerySet[ChatMessage]) -> QuerySet[ChatMessage]:
    """Narrow a message queryset to conversation turns outside evaluation and `SETUP` sessions (ADR-0051)."""
    return conversation_messages(exclude_non_activity(message_queryset, "chat__experiment_session__"))


def conversation_messages(message_queryset: QuerySet[ChatMessage]) -> QuerySet[ChatMessage]:
    """Narrow a message queryset to conversation turns, the human and AI messages."""
    return message_queryset.filter(message_type__in=CONVERSATION_MESSAGE_TYPES)


def tagged_conversation_exists_pair(team: Team, tag_ids: list[int], session_path: str) -> tuple[Exists, Exists]:
    """The (tag-on-chat, tag-on-message) `Exists` pair for querysets one step
    removed from the chat: an experiment or participant matches when any of
    its sessions' conversations carries one of `tag_ids`. `session_path` is
    the ``Chat``-side path back to the outer queryset's row
    (``"experiment_session__experiment"`` / ``"experiment_session__participant"``).
    Team scoping is the same as :func:`chat_tag_exists_pair`: both the link
    row and its tag must belong to the reading team."""
    chat_content_type = ContentType.objects.get_for_model(Chat)
    message_content_type = ContentType.objects.get_for_model(ChatMessage)
    tag_on_chat = Exists(
        CustomTaggedItem.objects.filter(
            team_id=team.id,
            tag__team_id=team.id,
            content_type=chat_content_type,
            object_id__in=Subquery(Chat.objects.filter(**{session_path: OuterRef(OuterRef("id"))}).values("id")),
            tag_id__in=tag_ids,
        )
    )
    tag_on_msg = Exists(
        CustomTaggedItem.objects.filter(
            team_id=team.id,
            tag__team_id=team.id,
            content_type=message_content_type,
            object_id__in=Subquery(
                ChatMessage.objects.filter(**{f"chat__{session_path}": OuterRef(OuterRef("id"))}).values("id")
            ),
            tag_id__in=tag_ids,
        )
    )
    return tag_on_chat, tag_on_msg


def chat_tag_exists_pair(team: Team, tag_ids: list[int], chat_id_ref: str) -> tuple[Exists, Exists]:
    """The (tag-on-chat, tag-on-message) `Exists` pair behind the session tag
    filter: a conversation matches when its chat, or any message in that chat,
    carries one of `tag_ids`. `chat_id_ref` is the outer queryset's path to
    the chat id (`"chat_id"` on sessions and messages). Both the link row and
    its tag are constrained to the reading team (`team_id` and
    `tag__team_id`), so a `CustomTaggedItem` row recorded under another team
    never qualifies a chat, and neither does a locally-recorded link whose tag
    belongs to another team. `Exists()` rather than join+distinct, matching
    the dashboard's tag filter.
    """
    chat_content_type = ContentType.objects.get_for_model(Chat)
    message_content_type = ContentType.objects.get_for_model(ChatMessage)
    tag_on_chat = Exists(
        CustomTaggedItem.objects.filter(
            team_id=team.id,
            tag__team_id=team.id,
            content_type=chat_content_type,
            object_id=OuterRef(chat_id_ref),
            tag_id__in=tag_ids,
        )
    )
    tag_on_msg = Exists(
        CustomTaggedItem.objects.filter(
            team_id=team.id,
            tag__team_id=team.id,
            content_type=message_content_type,
            object_id__in=Subquery(ChatMessage.objects.filter(chat=OuterRef(OuterRef(chat_id_ref))).values("id")),
            tag_id__in=tag_ids,
        )
    )
    return tag_on_chat, tag_on_msg
