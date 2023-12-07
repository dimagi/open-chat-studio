import csv
import io

from apps.experiments.models import Experiment


def experiment_to_message_export_rows(experiment: Experiment):
    for session in experiment.sessions.prefetch_related("chat", "chat__messages", "participant", "experiment_channel"):
        for message in session.chat.messages.all():
            yield [
                message.id,
                message.created_at,
                message.message_type,
                message.content,
                session.get_platform_name(),
                message.chat.id,
                # message.chat.name,
                str(message.chat.user),
                session.public_id,
                session.llm,
                experiment.public_id,
                experiment.name,
                session.participant.identifier if session.participant else None,
                session.participant.public_id if session.participant else None,
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
            "Session ID",
            "Session LLM",
            "Experiment ID",
            "Experiment Name",
            "Participant email",
            "Participant Public ID",
        ]
    )
    for row in experiment_to_message_export_rows(experiment):
        writer.writerow(row)
    return csv_in_memory
