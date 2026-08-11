"""Filter vocabulary for the usage-metrics read path (#3905).

`UsageFilters` is the contract the usage surfaces code against; its shape was
agreed in the design discussed on issue #3905. The tag helper is this app's
single definition of "this conversation carries one of these tags" - the same
chat-or-message match the dashboard's session filter and the cost read path
use.
"""

from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType
from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery

from apps.annotations.models import CustomTaggedItem
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.teams.models import Team


@dataclass(frozen=True)
class UsageFilters:
    """The filters every usage-metrics read honours, bundled so the metric
    functions take one argument instead of five.

    `experiment_ids`/`participant_ids`: `None` means "no filter"; an empty
    list means "requested but matched nobody" and yields empty results (the
    v2 usage API's resolved-handle semantics). `platform` is a single
    platform slug. `tag_ids` narrows to conversations whose chat or any
    message in it carries one of the tags; an empty list is treated as no
    filter.

    `include_archived` applies to experiment enumeration only: activity
    metrics count archived-chatbot activity regardless (ADR-0051). The
    default (`True`) matches the v2 usage API, which enumerates chatbots with
    `Experiment.objects.get_all()`; `filtered_querysets` defaults the other
    way, matching the dashboard's archived-excluding chatbot list. A caller
    passing `UsageFilters` through to `filtered_querysets` must pass this
    field explicitly rather than relying on either default.
    """

    experiment_ids: list[int] | None = None
    participant_ids: list[int] | None = None
    platform: str | None = None
    tag_ids: list[int] | None = None
    include_archived: bool = True


# The message types that are conversation turns. `system` messages are internal
# bookkeeping and are not counted by any metric (ADR-0051).
CONVERSATION_MESSAGE_TYPES = (ChatMessageType.HUMAN, ChatMessageType.AI)

# What makes a participant active: they authored a HUMAN message. Receiving AI
# output is not activity (ADR-0051). Every surface's participant count filters
# on this one predicate.
HUMAN_AUTHORED = Q(message_type=ChatMessageType.HUMAN)


def conversation_messages(message_queryset: QuerySet[ChatMessage]) -> QuerySet[ChatMessage]:
    """Narrow any team-scoped, windowed message queryset to conversation turns.
    Both surfaces route through this, so "which messages count" is defined once
    whether the caller starts from ``messages_queryset`` (the API) or from
    ``filtered_querysets`` (the dashboard)."""
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
