import csv
import io
from unittest.mock import MagicMock, patch

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
                    "message": "Message from user1@example.com",
                },
                {
                    "participant": "user2@gmail.com",
                    "message": "Message from user2@gmail.com",
                },
            ],
            [0],  # Include only first session
        ),
        # Test case: No sessions included (empty result)
        (
            [
                {
                    "participant": "support@example.com",
                    "message": "Critical customer issue",
                    "date": "2023-07-20",
                },
            ],
            [],  # Include no sessions
        ),
    ],
)
def test_filtered_export_with_mocked_filter(mock_get_filtered_sessions, session_configs, filtered_indices):
    """Test the export functionality with a mocked filter function that returns a subset of sessions."""
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

    filtered_session_ids = [sessions[i].id for i in filtered_indices]
    mock_queryset = MagicMock()
    mock_queryset.values_list.return_value = filtered_session_ids
    mock_get_filtered_sessions.return_value = mock_queryset

    csv_in_memory = filtered_export_to_csv(experiment, filtered_session_ids)
    csv_content = csv_in_memory.getvalue()
    csv_lines = csv_content.strip().split("\n") if csv_content.strip() else []
    assert len(csv_lines) == len(filtered_session_ids) + 1

    if filtered_indices:
        csv_reader = csv.reader(io.StringIO(csv_content))
        rows = list(csv_reader)[1:]  # Skip header
        assert len(rows) == len(filtered_indices)
        for i in filtered_indices:
            message = session_configs[i]["message"]
            matching_rows = [row for row in rows if message in row]
            assert len(matching_rows) > 0, f"Message for session {i} not found in CSV"
