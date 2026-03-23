import csv
import io
import json
from unittest.mock import Mock, patch

import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.export import filtered_export_to_csv
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
    # Each trace produces 2 rows (human + AI), plus 1 header
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


def test_trace_id_export():
    input_msg_legacy = Mock(
        id="msg1",
        message_type="human",
        content="Hello",
        created_at="2024-01-01",
        trace_info=[{"trace_id": "trace123"}],
        tags=Mock(all=Mock(return_value=[])),
        comments=Mock(all=Mock(return_value=[])),
    )
    output_msg_legacy = Mock(
        id="msg2",
        message_type="ai",
        content="Hi",
        created_at="2024-01-01",
        trace_info=[],
        tags=Mock(all=Mock(return_value=[])),
        comments=Mock(all=Mock(return_value=[])),
    )

    input_msg_langfuse = Mock(
        id="msg3",
        message_type="human",
        content="Hello again",
        created_at="2024-01-01",
        trace_info=[
            {"trace_id": "traceABC", "trace_provider": OCS_TRACE_PROVIDER},
            {"trace_id": "trace456", "trace_provider": "langfuse"},
        ],
        tags=Mock(all=Mock(return_value=[])),
        comments=Mock(all=Mock(return_value=[])),
    )
    output_msg_langfuse = Mock(
        id="msg4",
        message_type="ai",
        content="Hi again",
        created_at="2024-01-01",
        trace_info=[],
        tags=Mock(all=Mock(return_value=[])),
        comments=Mock(all=Mock(return_value=[])),
    )

    session = Mock(
        external_id="session123",
        state={},
        get_platform_name=Mock(return_value="TestPlatform"),
        participant=Mock(name="Test Participant", identifier="participant123", public_id="public123"),
        chat=Mock(tags=Mock(all=Mock(return_value=[])), comments=Mock(all=Mock(return_value=[]))),
    )

    trace1 = Mock(
        input_message=input_msg_legacy,
        output_message=output_msg_legacy,
        session=session,
        participant_data={},
        participant_data_diff=[],
        timestamp="2024-01-01T00:00:00",
    )
    trace2 = Mock(
        input_message=input_msg_langfuse,
        output_message=output_msg_langfuse,
        session=session,
        participant_data={},
        participant_data_diff=[],
        timestamp="2024-01-01T00:01:00",
    )

    experiment = Mock(public_id="exp123", name="Test Experiment")

    mock_traces_qs = Mock()
    mock_traces_qs.select_related.return_value = mock_traces_qs
    mock_traces_qs.prefetch_related.return_value = mock_traces_qs
    mock_traces_qs.order_by.return_value = [trace1, trace2]

    with patch("apps.experiments.export.Trace.objects.filter", return_value=mock_traces_qs):
        csv_in_memory = filtered_export_to_csv(experiment, Mock())
        rows = list(csv.reader(io.StringIO(csv_in_memory.getvalue()), delimiter=","))

    header = rows[0]
    trace_id_index = header.index("Trace ID")

    assert rows[1][trace_id_index] == "trace123"  # human msg from trace1 (legacy)
    assert rows[2][trace_id_index] == "trace123"  # ai msg from trace1 (same trace_id)
    assert rows[3][trace_id_index] == "trace456"  # human msg from trace2 (langfuse)
    assert rows[4][trace_id_index] == "trace456"  # ai msg from trace2 (same trace_id)


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
