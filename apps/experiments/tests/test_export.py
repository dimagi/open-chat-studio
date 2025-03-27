import csv
import io
from unittest.mock import Mock, patch

import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.export import filtered_export_to_csv
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
    # Mock experiment
    experiment = Mock()
    experiment.public_id = "exp123"
    experiment.name = "Test Experiment"
    experiment.get_llm_provider_model_name.return_value = "test-llm"

    # Mock session and chat
    session = Mock()
    session.external_id = "session123"
    session.get_platform_name.return_value = "TestPlatform"
    session.participant = Mock(name="Test Participant", identifier="participant123", public_id="pubic123")
    chat = Mock()
    chat.tags.all.return_value = []
    chat.comments.all.return_value = []
    session.chat = chat

    # Mock messages
    message_with_trace = Mock()
    message_with_trace.id = "msg1"
    message_with_trace.message_type = "human"
    message_with_trace.content = "Hello"
    message_with_trace.metadata = {"trace_info": {"trace_id": "trace123"}}
    message_with_trace.trace_info = {"trace_id": "trace123"}
    message_with_trace.tags.all.return_value = []
    message_with_trace.comments.all.return_value = []
    message_with_trace.chat = chat

    message_no_trace = Mock()
    message_no_trace.id = "msg2"
    message_no_trace.message_type = "ai"
    message_no_trace.content = "Hi"
    message_no_trace.metadata = {}
    message_no_trace.trace_info = None
    message_no_trace.tags.all.return_value = []
    message_no_trace.comments.all.return_value = []
    message_no_trace.chat = chat

    chat.messages.all.return_value = [message_with_trace, message_no_trace]

    sessions_queryset = Mock()
    sessions_queryset.prefetch_related.return_value = [session]
    csv_output = filtered_export_to_csv(experiment, sessions_queryset)
    csv_content = csv_output.getvalue()

    csv_reader = csv.reader(io.StringIO(csv_content), delimiter=",")
    rows = list(csv_reader)

    assert "Trace ID" in rows[0], "Trace ID not in header"
    assert rows[1][-1] == "trace123", "Trace ID not exported correctly"
    assert rows[2][-1] == "", "Empty trace ID not handled correctly"
