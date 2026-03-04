import csv
import io
from unittest.mock import Mock, patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.export import filtered_export_to_csv
from apps.experiments.models import ExperimentSession
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
        input_msg = ChatMessage.objects.create(
            chat=session.chat,
            content=config["message"],
            message_type=ChatMessageType.HUMAN,
        )
        output_msg = ChatMessage.objects.create(
            chat=session.chat,
            content=f"Response to {config['message']}",
            message_type=ChatMessageType.AI,
        )
        TraceFactory(
            experiment=experiment,
            session=session,
            participant=session.participant,
            team=team,
            input_message=input_msg,
            output_message=output_msg,
            participant_data={"name": config["participant"]},
        )

        sessions.append(session)
    if filtered_indices:
        filtered_queryset = experiment.sessions.filter(id__in=[sessions[i].id for i in filtered_indices])
    else:
        filtered_queryset = experiment.sessions.none()

    csv_in_memory = filtered_export_to_csv(experiment, filtered_queryset)
    csv_content = csv_in_memory.getvalue()
    csv_lines = csv_content.strip().split("\n") if csv_content.strip() else []
    # Each trace produces 2 rows (input + output), plus the header
    expected_rows = len(filtered_indices) * 2
    assert len(csv_lines) == expected_rows + 1

    if filtered_indices:
        csv_reader = csv.reader(io.StringIO(csv_content))
        rows = list(csv_reader)[1:]  # Skip header
        assert len(rows) == expected_rows
        for i in filtered_indices:
            message = session_configs[i]["message"]
            matching_rows = [row for row in rows if message in row]
            assert len(matching_rows) > 0, f"Message for session {i} not found in CSV"


@pytest.mark.django_db()
def test_export_query_count():
    """Verify that the export uses a bounded number of queries regardless of row count."""
    experiment = ExperimentFactory()
    team = experiment.team

    for i in range(20):
        session = ExperimentSessionFactory(
            experiment=experiment,
            team=team,
            experiment_channel=ExperimentChannelFactory(),
        )
        input_msg = ChatMessage.objects.create(
            chat=session.chat,
            content=f"input {i}",
            message_type=ChatMessageType.HUMAN,
        )
        output_msg = ChatMessage.objects.create(
            chat=session.chat,
            content=f"output {i}",
            message_type=ChatMessageType.AI,
        )
        TraceFactory(
            experiment=experiment,
            session=session,
            participant=session.participant,
            team=team,
            input_message=input_msg,
            output_message=output_msg,
        )

    sessions_qs = ExperimentSession.objects.filter(experiment=experiment)

    with CaptureQueriesContext(connection) as context:
        filtered_export_to_csv(experiment, sessions_qs)

    # 1 main query + ~10 prefetch queries = bounded constant.
    # Must not scale with the number of traces/sessions.
    assert len(context) == 11, f"Expected bounded queries, got {len(context)}"


@patch("apps.experiments.export.Trace.objects")
def test_trace_id_export(mock_trace_objects):
    """Test CSV export of trace IDs and participant data using mocked traces.

    Verifies three scenarios:
    - Legacy trace_info (no trace_provider): exports the trace_id and participant data snapshot
    - Mixed trace providers (OCS + langfuse): exports only the non-OCS trace_id, skips empty participant data
    - Empty trace_info with null participant data: exports empty strings for both columns
    """
    experiment = Mock(
        public_id="exp123", name="Test Experiment", get_llm_provider_model_name=Mock(return_value="test-llm")
    )
    session = Mock(
        external_id="session123",
        get_platform_name=Mock(return_value="TestPlatform"),
        participant=Mock(
            name="Test Participant",
            identifier="participant123",
            public_id="public123",
        ),
    )
    chat = Mock(tags=Mock(all=Mock(return_value=[])), comments=Mock(all=Mock(return_value=[])))

    def make_message(id, content, trace_info):
        return Mock(
            id=id,
            message_type="human",
            content=content,
            created_at="2024-01-01",
            trace_info=trace_info,
            tags=Mock(all=Mock(return_value=[])),
            comments=Mock(all=Mock(return_value=[])),
            chat=chat,
        )

    # Trace 1: legacy trace_info, with participant data
    msg1_input = make_message("msg1", "Hello", [{"trace_id": "trace123"}])
    msg1_output = make_message("msg1_out", "Hi back", [{"trace_id": "trace123"}])

    # Trace 2: mixed trace providers (only langfuse should be exported), no participant data
    msg2_input = make_message(
        "msg2",
        "Hello again",
        [
            {"trace_id": "traceABC", "trace_provider": OCS_TRACE_PROVIDER},
            {"trace_id": "trace456", "trace_provider": "langfuse"},
        ],
    )

    # Trace 3: no trace_info, no participant data
    msg3_input = make_message("msg3", "Bye", [])
    msg3_output = make_message("msg3_out", "Goodbye", [])

    mock_traces = [
        Mock(session=session, participant_data={"key": "value"}, input_message=msg1_input, output_message=msg1_output),
        Mock(session=session, participant_data={}, input_message=msg2_input, output_message=None),
        Mock(session=session, participant_data=None, input_message=msg3_input, output_message=msg3_output),
    ]

    # Chain the queryset methods to return our mock traces
    mock_qs = Mock()
    mock_trace_objects.filter.return_value = mock_qs
    mock_qs.select_related.return_value = mock_qs
    mock_qs.prefetch_related.return_value = mock_qs
    mock_qs.order_by.return_value = mock_traces

    rows = list(
        csv.reader(
            io.StringIO(filtered_export_to_csv(experiment, Mock()).getvalue()),
            delimiter=",",
        )
    )

    assert "Trace ID" in rows[0], "Trace ID not in header"
    trace_id_idx = rows[0].index("Trace ID")
    participant_data_idx = rows[0].index("Participant Data")

    # Trace 1: input + output (2 rows), legacy trace_id, has participant data
    assert rows[1][trace_id_idx] == "trace123"
    assert rows[1][participant_data_idx] == '{"key": "value"}'
    assert rows[2][trace_id_idx] == "trace123"
    assert rows[2][participant_data_idx] == '{"key": "value"}'

    # Trace 2: input only (1 row), langfuse trace_id, empty participant data
    assert rows[3][trace_id_idx] == "trace456"
    assert rows[3][participant_data_idx] == ""

    # Trace 3: input + output (2 rows), no trace_id, no participant data
    assert rows[4][trace_id_idx] == ""
    assert rows[4][participant_data_idx] == ""
    assert rows[5][trace_id_idx] == ""
    assert rows[5][participant_data_idx] == ""
