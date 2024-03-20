import csv
import io

from apps.annotations.models import Tag
from apps.experiments.models import Experiment


def _parse_tags(tags: list[Tag]) -> str:
    """Returns `tags` parsed into a single string in the format 'tag1, tag2, tag3'"""
    return ", ".join([t.name for t in tags])


def experiment_to_message_export_rows(experiment: Experiment):
    for session in experiment.sessions.prefetch_related(
        "chat", "chat__messages", "participant", "experiment_channel", "chat__tags", "chat__messages__tags"
    ):
        for message in session.chat.messages.all():
            yield [
                message.id,
                message.created_at,
                message.message_type,
                message.content,
                session.get_platform_name(),
                message.chat.id,
                str(message.chat.user),
                _parse_tags(message.chat.tags.all()),
                session.public_id,
                session.llm,
                experiment.public_id,
                experiment.name,
                session.participant.identifier if session.participant else None,
                session.participant.public_id if session.participant else None,
                _parse_tags(message.tags.all()),
            ]


def experiment_to_csv(experiment: Experiment) -> io.StringIO:
    csv_in_memory = io.StringIO()
    writer = csv.writer(csv_in_memory, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "Message ID",
            "Message Date",
            "Message Type",
            "Message Content",
            "Platform",
            "Chat ID",
            "Chat User",
            "Chat Tags",
            "Session ID",
            "Session LLM",
            "Experiment ID",
            "Experiment Name",
            "Participant email",
            "Participant Public ID",
            "Message Tags",
        ]
    )
    for row in experiment_to_message_export_rows(experiment):
        writer.writerow(row)
    return csv_in_memory
