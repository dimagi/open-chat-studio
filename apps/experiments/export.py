import csv
import gc
import gzip
import io
import json
import tempfile
from collections import OrderedDict
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import dictdiffer

from apps.analysis.translation import get_message_content
from apps.annotations.models import Tag, UserComment
from apps.chat.models import ChatMessage
from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.files.exports import csv_to_tempfile
from apps.service_providers.tracing import OCS_TRACE_PROVIDER
from apps.web.dynamic_filters.datastructures import FilterParams

EXPORT_CHUNK_SIZE = 1000
PROGRESS_UPDATE_INTERVAL = 100

# Ceiling on the per-session value cache. At roughly 2.4 KB per entry this caps the cache
# at a few MB regardless of how many sessions the export spans.
SESSION_CACHE_MAX_ENTRIES = 2000

UTF8_BOM = "\ufeff"  # Prepended to CSV exports so Excel detects UTF-8 encoding.


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
    return session_filter.apply(sessions_queryset, filter_params=FilterParams(query_params), timezone=timezone)


def _get_participant_data_for_message(message) -> dict:
    """Return participant data for a message by looking up its associated trace.

    For human (input) messages: returns the snapshot at the start of the trace.
    For AI (output) messages: applies the trace diff to get the end snapshot.
    """
    if message.is_human_message:
        traces = list(message.input_message_trace.all())
        if traces:
            return traces[0].participant_data or {}
    elif message.is_ai_message:
        traces = list(message.output_message_trace.all())
        if traces:
            trace = traces[0]
            start_data = trace.participant_data or {}
            if trace.participant_data_diff:
                return dictdiffer.patch(trace.participant_data_diff, start_data)
            return start_data
    return {}


@dataclass(frozen=True)
class ExportColumn:
    """A chat export column.

    Session-level values (``from_session``) are cached once per session; message-level
    values (``from_message``) are computed for every row.
    """

    key: str
    label: str
    from_message: Callable[[ChatMessage, Any, str | None], Any] | None = None
    from_session: Callable[[ExperimentSession], Any] | None = None
    prefetch: tuple[str, ...] = ()
    required: bool = False


EXPORT_COLUMNS: list[ExportColumn] = [
    ExportColumn(key="message_id", label="Message ID", from_message=lambda m, e, t: m.id, required=True),
    ExportColumn(key="message_date", label="Message Date", from_message=lambda m, e, t: m.created_at),
    ExportColumn(key="message_type", label="Message Type", from_message=lambda m, e, t: m.message_type, required=True),
    ExportColumn(
        key="message_content",
        label="Message Content",
        from_message=lambda m, e, t: get_message_content(m, t) if t else m.content,
    ),
    ExportColumn(key="platform", label="Platform", from_session=lambda s: s.get_platform_name()),
    ExportColumn(
        key="session_tags",
        label="Session Tags",
        from_session=lambda s: _format_tags(s.chat.tags.all()),
        prefetch=("chat__tags",),
    ),
    ExportColumn(
        key="session_comments",
        label="Session Comments",
        from_session=lambda s: _format_comments(s.chat.comments.all()),
        prefetch=("chat__comments", "chat__comments__user"),
    ),
    ExportColumn(key="session_id", label="Session ID", from_session=lambda s: s.external_id),
    ExportColumn(key="session_state", label="Session State", from_session=lambda s: json.dumps(s.state)),
    ExportColumn(key="chatbot_id", label="Chatbot ID", from_message=lambda m, e, t: e.public_id),
    ExportColumn(key="chatbot_name", label="Chatbot Name", from_message=lambda m, e, t: e.name),
    ExportColumn(key="participant_name", label="Participant Name", from_session=lambda s: s.participant.name),
    ExportColumn(
        key="participant_identifier",
        label="Participant Identifier",
        from_session=lambda s: s.participant.identifier,
    ),
    ExportColumn(
        key="participant_public_id",
        label="Participant Public ID",
        from_session=lambda s: s.participant.public_id,
    ),
    ExportColumn(
        key="message_tags",
        label="Message Tags",
        from_message=lambda m, e, t: _format_tags(m.tags.all()),
        prefetch=("tags",),
    ),
    ExportColumn(
        key="message_comments",
        label="Message Comments",
        from_message=lambda m, e, t: _format_comments(m.comments.all()),
        prefetch=("comments", "comments__user"),
    ),
    ExportColumn(key="trace_id", label="Trace ID", from_message=lambda m, e, t: _get_trace_id_for_export(m)),
    ExportColumn(
        key="participant_data",
        label="Participant Data",
        from_message=lambda m, e, t: json.dumps(_get_participant_data_for_message(m)),
        prefetch=("input_message_trace", "output_message_trace"),
    ),
]


def resolve_export_columns(keys: Iterable[str] | None = None) -> list[ExportColumn]:
    """Return the selected columns in export order, plus required ones. ``None`` selects all.

    Unknown keys are ignored.
    """
    if keys is None:
        return list(EXPORT_COLUMNS)
    selected = set(keys)
    return [column for column in EXPORT_COLUMNS if column.required or column.key in selected]


def count_export_messages(sessions_queryset) -> int:
    """Total number of messages an export of these sessions will produce.

    Used as the denominator for export progress reporting.
    """
    return _build_message_queryset(sessions_queryset).count()


def _build_message_queryset(sessions_queryset, columns: list[ExportColumn] = EXPORT_COLUMNS):
    """Return the ChatMessage queryset for export, ordered by pk for keyset pagination.

    Only the relations the given columns read are prefetched.
    """
    prefetch = [lookup for column in columns for lookup in column.prefetch]
    return (
        ChatMessage.objects.filter(
            chat__experiment_session__in=sessions_queryset,
        )
        .select_related(
            "chat",
            "chat__experiment_session",
            "chat__experiment_session__participant",
            "chat__experiment_session__experiment_channel",
        )
        .prefetch_related(*prefetch)
        .order_by("pk")
    )


def _get_export_header(columns: list[ExportColumn], translation_language=None) -> list[str]:
    header = [column.label for column in columns]
    if translation_language:
        header.append("Message Language")
        header.append("Original Message")
    return header


class _SessionCache:
    """Bounded LRU cache of the per-session values repeated on every row of that session.

    Messages are exported in order of global message pk, so a session's messages stay
    interleaved with other sessions' for the whole run and no entry is ever safely "done".
    An unbounded dict therefore grows with the session count (~2.4 KB per session), which
    is the one export memory term that scales with the dataset rather than with the output.

    Capping it trades a recomputation when an evicted session reappears for a fixed
    ceiling. A miss costs no extra query: the session and its chat tags/comments are
    already loaded on the message by ``_build_message_queryset``.
    """

    def __init__(self, columns: list[ExportColumn], max_entries: int | None = None):
        self._entries: OrderedDict[int, dict] = OrderedDict()
        self._session_getters = [(column.key, column.from_session) for column in columns if column.from_session]
        # Resolved at call time rather than bound as a default so the ceiling stays patchable.
        self._max_entries = SESSION_CACHE_MAX_ENTRIES if max_entries is None else max_entries

    def get(self, session) -> dict:
        try:
            entry = self._entries[session.id]
        except KeyError:
            entry = {key: getter(session) for key, getter in self._session_getters}
            self._entries[session.id] = entry
            if len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return entry
        self._entries.move_to_end(session.id)
        return entry

    def __len__(self) -> int:
        return len(self._entries)


def _yield_row_for_message(
    message, session_cache: _SessionCache, experiment, columns: list[ExportColumn], translation_language
) -> list:
    """Return an export row for a single message."""
    sc = session_cache.get(message.chat.experiment_session)
    row = [
        column.from_message(message, experiment, translation_language) if column.from_message else sc[column.key]
        for column in columns
    ]
    if translation_language:
        row.append(translation_language)
        row.append(message.content)
    return row


def generate_export_rows(
    experiment, sessions_queryset, translation_language=None, progress_callback=None, columns=None
) -> Generator[list]:
    """Yield the header row, then one data row per message across all matching sessions.

    Messages are processed in chunks of EXPORT_CHUNK_SIZE using keyset pagination so
    that memory usage stays bounded regardless of dataset size.  Session-level values
    (platform name, state JSON, participant fields, chat tags/comments) are cached the
    first time each session is encountered so they are serialised only once no matter
    how many messages belong to that session; see :class:`_SessionCache` for why that
    cache is bounded rather than keeping every session for the life of the export.

    ``columns`` is a list of column keys to include (see ``EXPORT_COLUMNS``); ``None`` includes all.
    """
    export_columns = resolve_export_columns(columns)
    yield _get_export_header(columns=export_columns, translation_language=translation_language)

    base_qs = _build_message_queryset(sessions_queryset, columns=export_columns)
    last_pk = 0
    session_cache = _SessionCache(columns=export_columns)
    processed = 0

    def report(count):
        if progress_callback:
            progress_callback(count)

    while True:
        chunk = list(base_qs.filter(pk__gt=last_pk)[:EXPORT_CHUNK_SIZE])
        if not chunk:
            break

        for message in chunk:
            yield _yield_row_for_message(
                message=message,
                session_cache=session_cache,
                experiment=experiment,
                columns=export_columns,
                translation_language=translation_language,
            )
            processed += 1
            if processed % PROGRESS_UPDATE_INTERVAL == 0:
                report(processed)

        last_pk = chunk[-1].pk
        done = len(chunk) < EXPORT_CHUNK_SIZE
        # Django model instances take part in reference cycles (``_state``, the
        # related-object and prefetch caches), so refcounting alone does not reclaim a
        # spent chunk -- it lingers two to three chunks behind until a generational pass
        # happens to run. Dropping the reference before collecting is what lets the
        # current chunk go too, which measured ~70% off peak memory for large exports.
        del chunk
        gc.collect()
        if done:
            break

    # Report the final tally so the last partial interval (and exports smaller than one
    # interval) still reach 100%. processed == 0 is a no-op since 0 % INTERVAL == 0.
    if processed % PROGRESS_UPDATE_INTERVAL != 0:
        report(processed)


def export_rows_to_csv_stream(rows: Iterator[list]) -> Generator[str]:
    """Convert an iterable of row lists into a stream of CSV-formatted strings.

    Each yielded string is one complete CSV line.  Suitable for use with
    Django's StreamingHttpResponse so the response is sent to the client
    incrementally rather than buffered entirely in memory.
    """
    yield UTF8_BOM
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
    for row in rows:
        writer.writerow(row)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def export_to_tempfile(
    experiment, sessions_queryset, translation_language=None, compress=False, progress_callback=None, columns=None
) -> io.BufferedRandom | tempfile.SpooledTemporaryFile[bytes]:
    """Write the CSV export to a temporary file and return it, seeked to 0.

    Use as a context manager (``with export_to_tempfile(...) as tmp:``) so that
    the file is closed and any on-disk data is cleaned up automatically.

    The returned file is opened in binary mode (``mode="wb+"``); callers should
    read raw bytes from it (e.g. ``tmp.read()`` returns ``bytes``).

    If ``compress=False`` (default), a SpooledTemporaryFile is used so small
    exports stay in memory while large ones spill to disk automatically.

    If ``compress=True``, the data is written as a gzip-compressed stream using a
    regular TemporaryFile (always disk-backed).  CSV data compresses very well
    (typically 80–90% reduction), which significantly reduces both S3 storage and
    download time for large exports.  The gzip stream is finalised (trailer written)
    before returning so the file is a valid, complete .gz archive.
    """
    rows = generate_export_rows(
        experiment=experiment,
        sessions_queryset=sessions_queryset,
        translation_language=translation_language,
        progress_callback=progress_callback,
        columns=columns,
    )
    if compress:
        # Use a plain TemporaryFile rather than SpooledTemporaryFile.  The
        # SpooledTemporaryFile + GzipFile combination has known edge cases around
        # flushing and the gzip trailer.  For compressed exports the file is always
        # large enough to justify disk storage anyway.
        #
        # gzip.open() used as a context manager closes the GzipFile (writing the
        # gzip trailer) but does NOT close the underlying TemporaryFile because we
        # passed a file object rather than a filename.  tmp stays open and seekable.
        tmp = tempfile.TemporaryFile(mode="wb+")  # noqa: SIM115
        with gzip.open(tmp, "wt", encoding="utf-8", newline="") as gz:
            writer = csv.writer(gz, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL)
            for row in rows:
                writer.writerow(row)
        tmp.seek(0)
        return tmp
    return csv_to_tempfile(lambda writer: writer.writerows(rows))


def _get_trace_id_for_export(message):
    """Returns the trace info from the message.
    This will return the first non-OCS trace info if it exists.
    """
    if not message:
        return ""

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
