import csv
import io

from apps.analysis.translation import get_message_content
from apps.annotations.models import Tag, UserComment
from apps.experiments.filters import DynamicExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing import OCS_TRACE_PROVIDER


def _format_tags(tags: list[Tag]) -> str:
    """Returns `tags` parsed into a single string in the format 'tag1, tag2, tag3'"""
    return ", ".join([t.name for t in tags])


def _format_comments(user_comments: list[UserComment]) -> str:
    """Combine `user_comments` into a single string that looks like this:
    <username_1>: "user 1's comment" | <username_2>: "user 2's comment" | <username_1>: "user 1's comment"
    """
    return " | ".join([str(comment) for comment in user_comments])


def get_filtered_sessions(experiment, query_params, timezone):
    sessions_queryset = ExperimentSession.objects.filter(experiment=experiment).select_related("participant__user")
    session_filter = DynamicExperimentSessionFilter(sessions_queryset, parsed_params=query_params, timezone=timezone)
    sessions_queryset = session_filter.apply()

    return sessions_queryset


def filtered_export_to_csv(experiment, sessions_queryset, translation_language=None):
    csv_in_memory = io.StringIO()
    writer = csv.writer(csv_in_memory, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)

    queryset = sessions_queryset.prefetch_related(
        "chat",
        "chat__messages",
        "participant",
        "experiment_channel",
        "chat__tags",
        "chat__messages__tags",
        "chat__messages__comments",
        "chat__messages__comments__user",
    )
    header = [
        "Message ID",
        "Message Date",
        "Message Type",
        "Message Content",
        "Platform",
        "Chat Tags",
        "Chat Comments",
        "Session ID",
        "Session LLM",
        "Experiment ID",
        "Experiment Name",
        "Participant Name",
        "Participant Identifier",
        "Participant Public ID",
        "Message Tags",
        "Message Comments",
        "Trace ID",
    ]
    if translation_language:
        header.append("Message Language")
        header.append("Original Message")

    writer.writerow(header)

    for session in queryset:
        for message in session.chat.messages.all():
            if translation_language:
                content = get_message_content(message, translation_language)
            else:
                content = message.content
            trace_id = _get_trace_id_for_export(message)
            row = [
                message.id,
                message.created_at,
                message.message_type,
                content,
                session.get_platform_name(),
                _format_tags(message.chat.tags.all()),
                _format_comments(message.chat.comments.all()),
                session.external_id,
                experiment.get_llm_provider_model_name(raises=False),
                experiment.public_id,
                experiment.name,
                session.participant.name,
                session.participant.identifier,
                session.participant.public_id,
                _format_tags(message.tags.all()),
                _format_comments(message.comments.all()),
                trace_id,
            ]
            if translation_language:
                row.append(translation_language)
                row.append(message.content)
            writer.writerow(row)
    return csv_in_memory


def _get_trace_id_for_export(message):
    """Returns the trace info from the message.
    This will return the first non-OCS trace info if it exists.
    """
    if trace_infos := message.trace_info:
        non_ocs_trace = [
            info
            for info in trace_infos
            if (
                not info.get("trace_provider")  # legacy data
                or info.get("trace_provider") != OCS_TRACE_PROVIDER  # exclude OCS trace provider
            )
        ]
        if non_ocs_trace:
            return non_ocs_trace[0].get("trace_id", "")
    return ""
