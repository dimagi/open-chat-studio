import csv
import io
from unittest.mock import Mock, patch

import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.export import filtered_export_to_csv
from apps.service_providers.tracing import OCS_TRACE_PROVIDER
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


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
    experiment = ExperimentFactory()
    team = experiment.team
    sessions = []

    for config in session_configs:
        session = ExperimentSessionFactory(
            experiment=experiment,
            team=team,
            experiment_channel=ExperimentChannelFactory(),
            participant__identifier=config["participant"],
        )
        ChatMessage.objects.create(
            chat=session.chat,
            content=config["message"],
            message_type=ChatMessageType.HUMAN,
        )

        sessions.append(session)
    if filtered_indices:
        filtered_queryset = experiment.sessions.filter(id__in=[sessions[i].id for i in filtered_indices])
    else:
        filtered_queryset = experiment.sessions.none()

    csv_in_memory = filtered_export_to_csv(experiment, filtered_queryset)
    csv_content = csv_in_memory.getvalue()
    csv_lines = csv_content.strip().split("\n") if csv_content.strip() else []
    assert len(csv_lines) == len(filtered_indices) + 1

    if filtered_indices:
        csv_reader = csv.reader(io.StringIO(csv_content))
        rows = list(csv_reader)[1:]  # Skip header
        assert len(rows) == len(filtered_indices)
        for i in filtered_indices:
            message = session_configs[i]["message"]
            matching_rows = [row for row in rows if message in row]
            assert len(matching_rows) > 0, f"Message for session {i} not found in CSV"


def test_trace_id_export():
    # Mock experiment and session
    experiment = Mock(
        public_id="exp123", name="Test Experiment", get_llm_provider_model_name=Mock(return_value="test-llm")
    )
    session = Mock(
        external_id="session123",
        get_platform_name=Mock(return_value="TestPlatform"),
        participant=Mock(name="Test Participant", identifier="participant123", public_id="public123"),
        chat=Mock(tags=Mock(all=Mock(return_value=[])), comments=Mock(all=Mock(return_value=[]))),
    )

    session.chat.messages.all.return_value = [
        # legacy data
        Mock(
            id="msg1",
            message_type="human",
            content="Hello",
            metadata={"trace_info": [{"trace_id": "trace123"}]},
            trace_info=[{"trace_id": "trace123"}],
            tags=Mock(all=Mock(return_value=[])),
            comments=Mock(all=Mock(return_value=[])),
            chat=session.chat,
        ),
        # new data with two traces (only the langfuse one should get exported)
        Mock(
            id="msg1",
            message_type="human",
            content="Hello",
            metadata={
                "trace_info": [
                    {"trace_id": "traceABC", "trace_provider": OCS_TRACE_PROVIDER},
                    {"trace_id": "trace456", "trace_provider": "langfuse"},
                ]
            },
            trace_info=[
                {"trace_id": "traceABC", "trace_provider": OCS_TRACE_PROVIDER},
                {"trace_id": "trace456", "trace_provider": "langfuse"},
            ],
            tags=Mock(all=Mock(return_value=[])),
            comments=Mock(all=Mock(return_value=[])),
            chat=session.chat,
        ),
        Mock(
            id="msg2",
            message_type="ai",
            content="Hi",
            metadata={},
            trace_info=[],
            tags=Mock(all=Mock(return_value=[])),
            comments=Mock(all=Mock(return_value=[])),
            chat=session.chat,
        ),
    ]

    rows = list(
        csv.reader(
            io.StringIO(
                filtered_export_to_csv(experiment, Mock(prefetch_related=Mock(return_value=[session]))).getvalue()
            ),
            delimiter=",",
        )
    )

    assert "Trace ID" in rows[0], "Trace ID not in header"
    assert rows[1][-1] == "trace123", "Trace ID not exported correctly"
    assert rows[2][-1] == "trace456", "Trace ID not exported correctly"
    assert rows[3][-1] == "", "Empty trace ID not handled correctly"
