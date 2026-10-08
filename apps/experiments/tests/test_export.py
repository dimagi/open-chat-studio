import csv
import io
import json
import weakref
from unittest.mock import MagicMock, Mock, patch

import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments import export as export_mod
from apps.experiments.export import (
    EXPORT_COLUMNS,
    SESSION_CACHE_MAX_ENTRIES,
    UTF8_BOM,
    _build_message_queryset,
    _SessionCache,
    count_export_messages,
    export_rows_to_csv_stream,
    generate_export_rows,
    resolve_export_columns,
)
from apps.experiments.models import ExperimentSession
from apps.service_providers.tracing import OCS_TRACE_PROVIDER
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.traces import TraceFactory


def _export_csv_text(experiment, sessions_queryset, translation_language=None) -> str:
    """Render an export to CSV text, minus the Excel BOM, for content assertions."""
    rows = generate_export_rows(experiment, sessions_queryset, translation_language)
    return "".join(export_rows_to_csv_stream(rows)).removeprefix(UTF8_BOM)


@pytest.mark.django_db()
@patch("apps.experiments.export.get_filtered_sessions")
@pytest.mark.parametrize(
    ("session_configs", "filtered_indices"),
    [
        # Test case: Multiple sessions, only first included
        (
            [
                {
                    "participant": "user1@example.com",
                    "message": "Knock knock",
                },
                {
                    "participant": "user2@gmail.com",
                    "message": "Who's there?",
                },
            ],
            [0],  # Include only first session
        ),
        # Test case: No sessions included (empty result)
        (
            [
                {
                    "participant": "support@example.com",
                    "message": "hello world!",
                    "date": "2023-07-20",
                },
            ],
            [],  # Include no sessions
        ),
    ],
)
def test_filtered_export_with_mocked_filter(mock_get_filtered_sessions, session_configs, filtered_indices):
    experiment = ExperimentFactory.create()
    team = experiment.team
    sessions = []

    for config in session_configs:
        session = ExperimentSessionFactory.create(
            experiment=experiment,
            team=team,
            experiment_channel=ExperimentChannelFactory.create(),
            participant__identifier=config["participant"],
        )
        human_msg = ChatMessage.objects.create(
            chat=session.chat,
            content=config["message"],
            message_type=ChatMessageType.HUMAN,
        )
        ai_msg = ChatMessage.objects.create(
            chat=session.chat,
            content=f"Response to {config['message']}",
            message_type=ChatMessageType.AI,
        )
        TraceFactory.create(
            experiment=experiment,
            session=session,
            participant=session.participant,
            team=team,
            input_message=human_msg,
            output_message=ai_msg,
        )

        sessions.append(session)
    if filtered_indices:
        filtered_queryset = experiment.sessions.filter(id__in=[sessions[i].id for i in filtered_indices])
    else:
        filtered_queryset = experiment.sessions.none()

    csv_content = _export_csv_text(experiment, filtered_queryset)
    csv_lines = csv_content.strip().split("\n") if csv_content.strip() else []
    # Each session produces 2 rows (human + AI message), plus 1 header
    expected_rows = len(filtered_indices) * 2 + 1
    assert len(csv_lines) == expected_rows

    if filtered_indices:
        csv_reader = csv.reader(io.StringIO(csv_content))
        rows = list(csv_reader)[1:]  # Skip header
        assert len(rows) == len(filtered_indices) * 2
        for i in filtered_indices:
            message = session_configs[i]["message"]
            matching_rows = [row for row in rows if message in row]
            assert len(matching_rows) > 0, f"Message for session {i} not found in CSV"


@pytest.mark.django_db()
def test_participant_data_export():
    experiment = ExperimentFactory.create()
    team = experiment.team
    session = ExperimentSessionFactory.create(
        experiment=experiment,
        team=team,
        experiment_channel=ExperimentChannelFactory.create(),
    )
    human_msg = ChatMessage.objects.create(
        chat=session.chat,
        content="Hello",
        message_type=ChatMessageType.HUMAN,
    )
    ai_msg = ChatMessage.objects.create(
        chat=session.chat,
        content="Hi there",
        message_type=ChatMessageType.AI,
    )
    TraceFactory.create(
        experiment=experiment,
        session=session,
        participant=session.participant,
        team=team,
        input_message=human_msg,
        output_message=ai_msg,
        participant_data={"name": "Alice", "age": 25},
        participant_data_diff=[["change", "age", [25, 26]]],
    )

    csv_reader = csv.reader(io.StringIO(_export_csv_text(experiment, experiment.sessions.all())))
    rows = list(csv_reader)

    header = rows[0]
    pd_index = header.index("Participant Data")

    # Human message row: start state
    human_row = rows[1]
    assert json.loads(human_row[pd_index]) == {"name": "Alice", "age": 25}

    # AI message row: end state (age changed from 25 to 26)
    ai_row = rows[2]
    assert json.loads(ai_row[pd_index]) == {"name": "Alice", "age": 26}


@pytest.mark.django_db()
def test_participant_data_export_empty_diff():
    experiment = ExperimentFactory.create()
    team = experiment.team
    session = ExperimentSessionFactory.create(
        experiment=experiment,
        team=team,
        experiment_channel=ExperimentChannelFactory.create(),
    )
    human_msg = ChatMessage.objects.create(
        chat=session.chat,
        content="Hello",
        message_type=ChatMessageType.HUMAN,
    )
    ai_msg = ChatMessage.objects.create(
        chat=session.chat,
        content="Hi there",
        message_type=ChatMessageType.AI,
    )
    TraceFactory.create(
        experiment=experiment,
        session=session,
        participant=session.participant,
        team=team,
        input_message=human_msg,
        output_message=ai_msg,
        participant_data={"name": "Alice"},
        participant_data_diff=[],
    )

    csv_reader = csv.reader(io.StringIO(_export_csv_text(experiment, experiment.sessions.all())))
    rows = list(csv_reader)

    header = rows[0]
    pd_index = header.index("Participant Data")

    # Both rows should have the same participant data
    assert json.loads(rows[1][pd_index]) == {"name": "Alice"}
    assert json.loads(rows[2][pd_index]) == {"name": "Alice"}


@pytest.mark.django_db()
def test_participant_data_export_empty_data():
    experiment = ExperimentFactory.create()
    team = experiment.team
    session = ExperimentSessionFactory.create(
        experiment=experiment,
        team=team,
        experiment_channel=ExperimentChannelFactory.create(),
    )
    human_msg = ChatMessage.objects.create(
        chat=session.chat,
        content="Hello",
        message_type=ChatMessageType.HUMAN,
    )
    ai_msg = ChatMessage.objects.create(
        chat=session.chat,
        content="Hi there",
        message_type=ChatMessageType.AI,
    )
    TraceFactory.create(
        experiment=experiment,
        session=session,
        participant=session.participant,
        team=team,
        input_message=human_msg,
        output_message=ai_msg,
        participant_data={},
        participant_data_diff=[],
    )

    csv_reader = csv.reader(io.StringIO(_export_csv_text(experiment, experiment.sessions.all())))
    rows = list(csv_reader)

    header = rows[0]
    pd_index = header.index("Participant Data")

    assert json.loads(rows[1][pd_index]) == {}
    assert json.loads(rows[2][pd_index]) == {}


def test_trace_id_resolved_per_message_trace_info():
    """Each message's Trace ID column comes from its own trace_info, not from a paired message.

    Verifies three cases:
    - Legacy trace_info (no trace_provider): trace_id is used as-is.
    - Multiple providers: OCS entries are excluded; the first non-OCS entry wins.
    - Messages with no trace_info (e.g. AI responses): Trace ID column is empty.
    """
    session = Mock(
        id=1,
        external_id="session123",
        state={},
        get_platform_name=Mock(return_value="TestPlatform"),
        participant=Mock(name="Test Participant", identifier="participant123", public_id="public123"),
        chat=Mock(tags=Mock(all=Mock(return_value=[])), comments=Mock(all=Mock(return_value=[]))),
    )
    empty_trace = Mock(participant_data={}, participant_data_diff=[])

    def make_message(msg_id, is_human, content, trace_info):
        msg = Mock(
            id=msg_id,
            message_type="human" if is_human else "ai",
            content=content,
            created_at="2024-01-01",
            trace_info=trace_info,
            is_human_message=is_human,
            is_ai_message=not is_human,
            tags=Mock(all=Mock(return_value=[])),
            comments=Mock(all=Mock(return_value=[])),
        )
        msg.chat.experiment_session = session
        if is_human:
            msg.input_message_trace.all.return_value = [empty_trace]
        else:
            msg.output_message_trace.all.return_value = [empty_trace]
        return msg

    messages = [
        make_message("msg1", True, "Hello", [{"trace_id": "trace123"}]),
        make_message("msg2", False, "Hi", []),
        make_message(
            "msg3",
            True,
            "Hello again",
            [
                {"trace_id": "traceABC", "trace_provider": OCS_TRACE_PROVIDER},
                {"trace_id": "trace456", "trace_provider": "langfuse"},
            ],
        ),
        make_message("msg4", False, "Hi again", []),
    ]

    experiment = Mock(public_id="exp123", name="Test Experiment")

    # Build a chainable mock queryset that supports the keyset-pagination pattern:
    # ChatMessage.objects.filter(...).select_related(...).prefetch_related(...).order_by("pk")
    # then base_qs.filter(pk__gt=last_pk)[:CHUNK_SIZE] → messages
    mock_messages_qs = MagicMock()
    mock_messages_qs.select_related.return_value = mock_messages_qs
    mock_messages_qs.prefetch_related.return_value = mock_messages_qs
    mock_messages_qs.order_by.return_value = mock_messages_qs
    mock_messages_qs.filter.return_value.__getitem__ = Mock(return_value=messages)

    with patch("apps.experiments.export.ChatMessage.objects.filter", return_value=mock_messages_qs):
        csv_text = _export_csv_text(experiment, Mock())
        rows = list(csv.reader(io.StringIO(csv_text), delimiter=","))

    header = rows[0]
    trace_id_index = header.index("Trace ID")

    # Each message's trace_id comes from its own trace_info; AI messages with no
    # trace_info get an empty string.
    assert rows[1][trace_id_index] == "trace123"  # human msg (legacy trace_info)
    assert rows[2][trace_id_index] == ""  # ai msg has no trace_info
    assert rows[3][trace_id_index] == "trace456"  # human msg (langfuse, OCS excluded)
    assert rows[4][trace_id_index] == ""  # ai msg has no trace_info


@pytest.mark.django_db()
def test_session_state_export():
    experiment = ExperimentFactory.create()
    team = experiment.team
    session = ExperimentSessionFactory.create(
        experiment=experiment,
        team=team,
        experiment_channel=ExperimentChannelFactory.create(),
        state={"key": "value"},
    )
    human_msg = ChatMessage.objects.create(
        chat=session.chat,
        content="Hello",
        message_type=ChatMessageType.HUMAN,
    )
    ai_msg = ChatMessage.objects.create(
        chat=session.chat,
        content="Hi there",
        message_type=ChatMessageType.AI,
    )
    TraceFactory.create(
        experiment=experiment,
        session=session,
        participant=session.participant,
        team=team,
        input_message=human_msg,
        output_message=ai_msg,
    )

    csv_reader = csv.reader(io.StringIO(_export_csv_text(experiment, experiment.sessions.all())))
    rows = list(csv_reader)

    header = rows[0]
    state_index = header.index("Session State")

    assert json.loads(rows[1][state_index]) == {"key": "value"}
    assert json.loads(rows[2][state_index]) == {"key": "value"}


def test_export_rows_to_csv_stream_starts_with_utf8_bom():
    chunks = list(export_rows_to_csv_stream(iter([])))
    assert chunks[0] == UTF8_BOM, "First chunk must be a UTF-8 BOM so Excel reads the file correctly"


def test_export_rows_to_csv_stream_preserves_special_characters():
    rows = [["Message"], ["I’m at the café"]]
    output = "".join(export_rows_to_csv_stream(iter(rows)))
    assert "I’m at the café" in output


def _make_session_with_messages(count: int):
    session = ExperimentSessionFactory.create()
    for i in range(count):
        ChatMessage.objects.create(
            chat=session.chat,
            content=f"message {i}",
            message_type=ChatMessageType.HUMAN,
        )
    return session


@pytest.mark.django_db()
def test_count_export_messages():
    session = _make_session_with_messages(3)
    assert count_export_messages(session.experiment.sessions.all()) == 3


def test_session_cache_evicts_oldest_beyond_max_entries():
    """The cache is an LRU with a hard ceiling, so it can't grow with the session count."""
    cache = _SessionCache(columns=EXPORT_COLUMNS, max_entries=2)
    sessions = [Mock(id=i, external_id=f"s{i}", state={}, get_platform_name=Mock(return_value="Web")) for i in range(3)]
    for session in sessions:
        session.participant = Mock(name=f"p{session.id}", identifier=f"p{session.id}", public_id=f"pub{session.id}")
        session.chat = Mock(tags=Mock(all=Mock(return_value=[])), comments=Mock(all=Mock(return_value=[])))

    first = cache.get(sessions[0])
    cache.get(sessions[1])
    assert cache.get(sessions[0]) is first, "a cache hit must reuse the existing entry"

    # session 0 was just used, so session 1 is the least-recently-used and gets evicted.
    cache.get(sessions[2])
    assert len(cache) == 2
    assert cache.get(sessions[0]) is first
    assert cache.get(sessions[1]) is not None, "an evicted session is simply recomputed"
    assert len(cache) == 2


def test_session_cache_default_ceiling_comes_from_module_constant():
    assert _SessionCache(columns=EXPORT_COLUMNS)._max_entries == SESSION_CACHE_MAX_ENTRIES


@pytest.mark.django_db()
def test_export_rows_correct_when_sessions_exceed_cache_ceiling():
    """Rows stay correct for sessions evicted from the cache and re-encountered later.

    Messages are ordered by global message pk, so sessions interleave: with a ceiling
    below the session count, entries get evicted and rebuilt mid-export. Each row must
    still carry its own session's values, not a neighbour's.
    """
    experiment = ExperimentFactory.create()
    sessions = [
        ExperimentSessionFactory.create(
            experiment=experiment, team=experiment.team, participant__identifier=f"user{i}", state={"idx": i}
        )
        for i in range(4)
    ]
    # Interleave: one message per session per round, so pk order cycles through sessions.
    for round_no in range(3):
        for i, session in enumerate(sessions):
            ChatMessage.objects.create(
                chat=session.chat, content=f"s{i}-r{round_no}", message_type=ChatMessageType.HUMAN
            )

    with patch("apps.experiments.export.SESSION_CACHE_MAX_ENTRIES", 2):
        csv_reader = csv.reader(io.StringIO(_export_csv_text(experiment, experiment.sessions.all())))
        rows = list(csv_reader)

    header = rows[0]
    state_index = header.index("Session State")
    identifier_index = header.index("Participant Identifier")
    content_index = header.index("Message Content")

    assert len(rows) == 1 + 4 * 3
    for row in rows[1:]:
        # "s<i>-r<n>" encodes the owning session, so each row's own values must match it.
        expected_idx = int(row[content_index].split("-")[0].removeprefix("s"))
        assert json.loads(row[state_index]) == {"idx": expected_idx}
        assert row[identifier_index] == f"user{expected_idx}"


@pytest.mark.django_db()
def test_generate_export_rows_releases_spent_chunks():
    """A written-out chunk must be released, not left for some later generational pass.

    Django model instances sit in reference cycles, so dropping the last strong reference
    is not enough on its own. Weakrefs to the first chunk's messages assert release
    directly; counting live ChatMessage instances process-wide would instead depend on
    whatever other tests happen to be holding, which is not this code's business.
    """
    chunk_size = 5
    session = _make_session_with_messages(25)
    refs = []
    real_yield = export_mod._yield_row_for_message

    def recording_yield(message, **kwargs):
        refs.append(weakref.ref(message))
        return real_yield(message=message, **kwargs)

    first_chunk_alive_later = None
    with (
        patch("apps.experiments.export.EXPORT_CHUNK_SIZE", chunk_size),
        patch("apps.experiments.export._yield_row_for_message", recording_yield),
    ):
        for _ in generate_export_rows(session.experiment, session.experiment.sessions.all()):
            # One message into the third chunk, both earlier chunks have been collected.
            # (Chunk N's last message stays reachable via the loop variable until the
            # following chunk rebinds it, so it is chunk N+1's collect that frees it --
            # hence checking two chunks out rather than one.)
            if len(refs) == chunk_size * 2 + 1 and first_chunk_alive_later is None:
                first_chunk_alive_later = sum(1 for ref in refs[:chunk_size] if ref() is not None)

    assert first_chunk_alive_later == 0, (
        f"{first_chunk_alive_later}/{chunk_size} messages from the first chunk were still alive "
        "two chunks later -- spent chunks are not being released"
    )


FULL_EXPORT_HEADER = [
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


def test_export_columns_registry_matches_full_header():
    assert [column.label for column in EXPORT_COLUMNS] == FULL_EXPORT_HEADER


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("columns", "expected_header"),
    [
        pytest.param(None, FULL_EXPORT_HEADER, id="none_means_all"),
        pytest.param(
            ["message_content"],
            ["Message ID", "Message Type", "Message Content"],
            id="required_columns_always_included",
        ),
        pytest.param(
            ["participant_identifier", "message_content", "session_state"],
            ["Message ID", "Message Type", "Message Content", "Session State", "Participant Identifier"],
            id="registry_order_not_selection_order",
        ),
        pytest.param(
            ["bogus", "message_date"], ["Message ID", "Message Date", "Message Type"], id="unknown_keys_ignored"
        ),
    ],
)
def test_generate_export_rows_selected_columns(columns, expected_header):
    session = ExperimentSessionFactory.create(participant__identifier="alice", state={"step": 1})
    message = ChatMessage.objects.create(chat=session.chat, content="hello", message_type=ChatMessageType.HUMAN)

    header, row = list(generate_export_rows(session.experiment, session.experiment.sessions.all(), columns=columns))

    assert header == expected_header
    values = dict(zip(header, row, strict=True))
    assert values["Message ID"] == message.id
    expected_values = {
        "Message Content": "hello",
        "Message Type": ChatMessageType.HUMAN,
        "Session State": json.dumps({"step": 1}),
        "Participant Identifier": "alice",
    }
    for label, expected in expected_values.items():
        if label in values:
            assert values[label] == expected


@pytest.mark.django_db()
def test_unselected_columns_are_not_computed():
    session = _make_session_with_messages(2)
    with (
        patch("apps.experiments.export._get_participant_data_for_message") as participant_data_mock,
        patch("apps.experiments.export._get_trace_id_for_export") as trace_id_mock,
    ):
        list(generate_export_rows(session.experiment, session.experiment.sessions.all(), columns=["message_content"]))

    participant_data_mock.assert_not_called()
    trace_id_mock.assert_not_called()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("columns", "expected_prefetches"),
    [
        pytest.param(["message_content"], set(), id="no_prefetch_needed"),
        pytest.param(
            ["participant_data"], {"input_message_trace", "output_message_trace"}, id="participant_data_traces"
        ),
        pytest.param(["session_tags"], {"chat__tags"}, id="session_tags"),
    ],
)
def test_message_queryset_prefetches_only_selected_columns(columns, expected_prefetches):
    queryset = _build_message_queryset(ExperimentSession.objects.none(), columns=resolve_export_columns(columns))
    assert set(queryset._prefetch_related_lookups) == expected_prefetches
