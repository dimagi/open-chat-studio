import csv
import io
import json
from unittest.mock import MagicMock, Mock, patch

import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.export import (
    UTF8_BOM,
    count_export_messages,
    export_rows_to_csv_stream,
    filtered_export_to_csv,
    generate_export_rows,
)
from apps.service_providers.tracing import OCS_TRACE_PROVIDER
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.traces import TraceFactory


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

    csv_in_memory = filtered_export_to_csv(experiment, filtered_queryset)
    csv_content = csv_in_memory.getvalue()
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

    csv_in_memory = filtered_export_to_csv(experiment, experiment.sessions.all())
    csv_reader = csv.reader(io.StringIO(csv_in_memory.getvalue()))
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

    csv_in_memory = filtered_export_to_csv(experiment, experiment.sessions.all())
    csv_reader = csv.reader(io.StringIO(csv_in_memory.getvalue()))
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

    csv_in_memory = filtered_export_to_csv(experiment, experiment.sessions.all())
    csv_reader = csv.reader(io.StringIO(csv_in_memory.getvalue()))
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
        csv_in_memory = filtered_export_to_csv(experiment, Mock())
        rows = list(csv.reader(io.StringIO(csv_in_memory.getvalue()), delimiter=","))

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

    csv_in_memory = filtered_export_to_csv(experiment, experiment.sessions.all())
    csv_reader = csv.reader(io.StringIO(csv_in_memory.getvalue()))
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


@pytest.mark.django_db()
def test_generate_export_rows_reports_progress_per_message():
    """progress_callback is called once per message with the cumulative message count."""
    session = _make_session_with_messages(5)
    calls = []

    with patch("apps.experiments.export.EXPORT_CHUNK_SIZE", 2):
        list(
            generate_export_rows(
                session.experiment,
                session.experiment.sessions.all(),
                progress_callback=calls.append,
            )
        )

    assert calls == [1, 2, 3, 4, 5]
