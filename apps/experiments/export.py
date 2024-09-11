import csv
import io

from apps.annotations.models import Tag, UserComment
from apps.experiments.models import Experiment


def _format_tags(tags: list[Tag]) -> str:
    """Returns `tags` parsed into a single string in the format 'tag1, tag2, tag3'"""
    return ", ".join([t.name for t in tags])


def _format_comments(user_comments: list[UserComment]) -> str:
    """Combine `user_comments` into a single string that looks like this:
    <username_1>: "user 1's comment" | <username_2>: "user 2's comment" | <username_1>: "user 1's comment"
    """
    return " | ".join([str(comment) for comment in user_comments])


def experiment_to_message_export_rows(experiment: Experiment, tags: list[str] = None, participant: str = None):
    queryset = experiment.sessions.prefetch_related(
        "chat",
        "chat__messages",
        "participant",
        "experiment_channel",
        "chat__tags",
        "chat__messages__tags",
        "chat__messages__comments",
        "chat__messages__comments__user",
    )
    if tags:
        queryset = queryset.filter(chat__tags__name__in=tags)
    if participant:
        queryset = queryset.filter(participant__identifier=participant)

    yield [
        "Message ID",
        "Message Date",
        "Message Type",
        "Message Content",
        "Platform",
        "Chat ID",
        "Chat User",
        "Chat Tags",
        "Chat Comments",
        "Session ID",
        "Session LLM",
        "Experiment ID",
        "Experiment Name",
        "Participant Identifier",
        "Participant Public ID",
        "Message Tags",
        "Message Comments",
    ]

    for session in queryset:
        for message in session.chat.messages.all():
            yield [
                message.id,
                message.created_at,
                message.message_type,
                message.content,
                session.get_platform_name(),
                message.chat.id,
                str(session.participant.user),
                _format_tags(message.chat.tags.all()),
                _format_comments(message.chat.comments.all()),
                session.external_id,
                experiment.llm,
                experiment.public_id,
                experiment.name,
                session.participant.identifier,
                session.participant.public_id,
                _format_tags(message.tags.all()),
                _format_comments(message.comments.all()),
            ]


def experiment_to_csv(experiment: Experiment, tags: list[str] = None, participant: str = None) -> io.StringIO:
    csv_in_memory = io.StringIO()
    writer = csv.writer(csv_in_memory, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    for row in experiment_to_message_export_rows(experiment, tags=tags, participant=participant):
        writer.writerow(row)
    return csv_in_memory
