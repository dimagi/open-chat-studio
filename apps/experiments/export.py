import csv
import io
import json

import dictdiffer

from apps.analysis.translation import get_message_content
from apps.annotations.models import Tag, UserComment
from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing import OCS_TRACE_PROVIDER
from apps.trace.models import Trace
from apps.web.dynamic_filters.datastructures import FilterParams


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
    session_filter = ExperimentSessionFilter()
    sessions_queryset = session_filter.apply(
        sessions_queryset, filter_params=FilterParams(query_params), timezone=timezone
    )

    return sessions_queryset


def _get_participant_data_for_trace(trace):
    """Returns (start_data, end_data) for a trace.

    start_data is the participant_data snapshot at the beginning of the trace.
    end_data is computed by applying participant_data_diff to start_data.
    If the diff is empty, end_data equals start_data.
    """
    start_data = trace.participant_data or {}
    if trace.participant_data_diff:
        end_data = dictdiffer.patch(trace.participant_data_diff, start_data)
    else:
        end_data = start_data
    return start_data, end_data


def filtered_export_to_csv(experiment, sessions_queryset, translation_language=None):
    csv_in_memory = io.StringIO()
    writer = csv.writer(csv_in_memory, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)

    # NOTE: This export is trace-driven rather than message-driven. Each trace produces two rows
    # (input + output). Messages not associated with a trace will not appear in the export.
    traces = (
        Trace.objects.filter(
            session__in=sessions_queryset,
            input_message__isnull=False,
        )
        .select_related(
            "input_message",
            "output_message",
            "session",
            "session__participant",
            "session__experiment_channel",
        )
        .prefetch_related(
            "input_message__tags",
            "input_message__comments",
            "input_message__comments__user",
            "output_message__tags",
            "output_message__comments",
            "output_message__comments__user",
            "session__chat__tags",
            "session__chat__comments",
            "session__chat__comments__user",
        )
        .order_by("timestamp")
    )

    header = [
        "Message ID",
        "Message Date",
        "Message Type",
        "Message Content",
        "Platform",
        "Session Tags",
        "Session Comments",
        "Session ID",
        "Session Status",
        "Chatbot ID",
        "Chatbot Name",
        "Participant Name",
        "Participant Identifier",
        "Participant Public ID",
        "Message Tags",
        "Message Comments",
        "Trace ID",
        "Participant Data",
    ]
    if translation_language:
        header.append("Message Language")
        header.append("Original Message")

    writer.writerow(header)

    for trace in traces:
        start_data, end_data = _get_participant_data_for_trace(trace)
        trace_id = _get_trace_id_for_export(trace.input_message)  # safe: filtered out NULLs above
        session = trace.session

        for message, participant_data in [
            (trace.input_message, start_data),
            (trace.output_message, end_data),
        ]:
            if message is None:
                continue

            if translation_language:
                content = get_message_content(message, translation_language)
            else:
                content = message.content
            row = [
                message.id,
                message.created_at,
                message.message_type,
                content,
                session.get_platform_name(),
                _format_tags(session.chat.tags.all()),
                _format_comments(session.chat.comments.all()),
                session.external_id,
                session.status,
                experiment.public_id,
                experiment.name,
                session.participant.name,
                session.participant.identifier,
                session.participant.public_id,
                _format_tags(message.tags.all()),
                _format_comments(message.comments.all()),
                trace_id,
                json.dumps(participant_data),
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
