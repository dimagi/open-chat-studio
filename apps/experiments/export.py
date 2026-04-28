import csv
import io
import json
import tempfile
from collections.abc import Generator, Iterator

_SPOOLED_MAX_BYTES = 10 * 1024 * 1024  # 10 MB threshold before spilling to disk

import dictdiffer

from apps.analysis.translation import get_message_content
from apps.annotations.models import Tag, UserComment
from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing import OCS_TRACE_PROVIDER
from apps.trace.models import Trace
from apps.web.dynamic_filters.datastructures import FilterParams

EXPORT_CHUNK_SIZE = 1000


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


def _build_trace_queryset(sessions_queryset):
    """Return the base Trace queryset for export, ordered by pk for keyset pagination."""
    return (
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
            "session__chat",  # OneToOneField: explicit JOIN beats implicit prefetch
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
        .order_by("pk")
    )


def _get_export_header(translation_language=None):
    header = [
        "Message ID",
        "Message Date",
        "Message Type",
        "Message Content",
        "Platform",
        "Session Tags",
        "Session Comments",
        "Session ID",
        "Session State",
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
    return header


def _build_session_cache_entry(session) -> dict:
    """Compute and return the per-session values used in every export row for that session."""
    return {
        "platform": session.get_platform_name(),
        "session_tags": _format_tags(session.chat.tags.all()),
        "session_comments": _format_comments(session.chat.comments.all()),
        "external_id": session.external_id,
        "state": json.dumps(session.state),
        "participant_name": session.participant.name,
        "participant_identifier": session.participant.identifier,
        "participant_public_id": session.participant.public_id,
    }


def _build_message_row(message, participant_data, sc, experiment, trace_id, translation_language) -> list | None:
    """Return an export row list for *message*, or None if the message is absent."""
    if message is None:
        return None
    content = get_message_content(message, translation_language) if translation_language else message.content
    row = [
        message.id,
        message.created_at,
        message.message_type,
        content,
        sc["platform"],
        sc["session_tags"],
        sc["session_comments"],
        sc["external_id"],
        sc["state"],
        experiment.public_id,
        experiment.name,
        sc["participant_name"],
        sc["participant_identifier"],
        sc["participant_public_id"],
        _format_tags(message.tags.all()),
        _format_comments(message.comments.all()),
        trace_id,
        json.dumps(participant_data),
    ]
    if translation_language:
        row.append(translation_language)
        row.append(message.content)
    return row


def _yield_rows_for_trace(trace, session_cache, experiment, translation_language) -> Generator[list, None, None]:
    """Yield one export row per message (input + output) for a single trace."""
    start_data, end_data = _get_participant_data_for_trace(trace)
    trace_id = _get_trace_id_for_export(trace.input_message)
    session = trace.session

    if session.id not in session_cache:
        session_cache[session.id] = _build_session_cache_entry(session)
    sc = session_cache[session.id]

    for message, participant_data in [(trace.input_message, start_data), (trace.output_message, end_data)]:
        row = _build_message_row(message, participant_data, sc, experiment, trace_id, translation_language)
        if row is not None:
            yield row


def generate_export_rows(
    experiment, sessions_queryset, translation_language=None
) -> Generator[list, None, None]:
    """Yield the header row, then one data row per message across all matching traces.

    Traces are processed in chunks of EXPORT_CHUNK_SIZE using keyset pagination so
    that memory usage stays bounded regardless of dataset size.  Session-level values
    (platform name, state JSON, participant fields, chat tags/comments) are cached the
    first time each session is encountered so they are serialised only once no matter
    how many traces belong to that session.
    """
    yield _get_export_header(translation_language)

    base_qs = _build_trace_queryset(sessions_queryset)
    last_pk = 0
    # Cache stores plain strings/dicts (not ORM objects) so its footprint is small
    # even for experiments with many sessions.
    session_cache: dict[int, dict] = {}

    while True:
        chunk = list(base_qs.filter(pk__gt=last_pk)[:EXPORT_CHUNK_SIZE])
        if not chunk:
            break

        for trace in chunk:
            yield from _yield_rows_for_trace(trace, session_cache, experiment, translation_language)

        last_pk = chunk[-1].pk
        if len(chunk) < EXPORT_CHUNK_SIZE:
            # Partial chunk means we've reached the end; skip the extra query.
            break


def export_rows_to_csv_stream(rows: Iterator[list]) -> Generator[str, None, None]:
    """Convert an iterable of row lists into a stream of CSV-formatted strings.

    Each yielded string is one complete CSV line.  Suitable for use with
    Django's StreamingHttpResponse so the response is sent to the client
    incrementally rather than buffered entirely in memory.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    for row in rows:
        writer.writerow(row)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def export_to_tempfile(experiment, sessions_queryset, translation_language=None) -> tempfile.SpooledTemporaryFile:
    """Write the CSV export to a spooled temporary file and return it, seeked to 0.

    Use as a context manager (``with export_to_tempfile(...) as tmp:``) so that
    the file is closed and any spilled data on disk is removed automatically.
    Using a spooled file means small exports stay in memory while large ones spill
    to disk automatically, avoiding a single large in-memory allocation.

    The returned file is opened in binary mode (``mode="wb+"``); callers should
    read raw bytes from it (e.g. ``tmp.read()`` returns ``bytes``).
    """
    tmp = tempfile.SpooledTemporaryFile(max_size=_SPOOLED_MAX_BYTES, mode="wb+")
    # Wrap in a TextIOWrapper so csv.writer receives a text-mode file object.
    # detach=False: the wrapper does not close the underlying SpooledTemporaryFile
    # when it is itself garbage-collected, preserving the caller's ability to seek/read.
    text_wrapper = io.TextIOWrapper(tmp, encoding="utf-8", newline="")
    writer = csv.writer(text_wrapper, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    for row in generate_export_rows(experiment, sessions_queryset, translation_language):
        writer.writerow(row)
    text_wrapper.flush()
    text_wrapper.detach()  # release the wrapper without closing the underlying file
    tmp.seek(0)
    return tmp


def filtered_export_to_csv(experiment, sessions_queryset, translation_language=None):
    """Build a complete CSV in a StringIO buffer and return it.

    For large datasets prefer generate_export_rows() + export_rows_to_csv_stream()
    to avoid buffering the entire export in memory.  This function is retained for
    backward compatibility with callers that expect a StringIO return value.
    """
    csv_in_memory = io.StringIO()
    writer = csv.writer(csv_in_memory, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    for row in generate_export_rows(experiment, sessions_queryset, translation_language):
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
