import csv
import datetime
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
    ("session_configs", "filtered_indices", "expected_row_count"),
    [
        # Test case 1: Multiple sessions, all included
        (
            [
                {
                    "participant": "user1@example.com",
                    "message": "Message from user1@example.com",
                    "date": "2023-06-15",
                },
                {
                    "participant": "user2@gmail.com",
                    "message": "Message from user2@gmail.com",
                    "date": "2023-06-10",
                },
            ],
            [0, 1],  # Include both sessions
            3,  # Header + 2 rows
        ),
        # Test case 2: Multiple sessions, only first included
        (
            [
                {
                    "participant": "user1@example.com",
                    "message": "Message from user1@example.com",
                    "date": "2023-06-15",
                },
                {
                    "participant": "user2@gmail.com",
                    "message": "Message from user2@gmail.com",
                    "date": "2023-06-10",
                },
            ],
            [0],  # Include only first session
            2,  # Header + 1 row
        ),
        # Test case 3: Multiple sessions, only second included
        (
            [
                {
                    "participant": "user1@example.com",
                    "message": "Message from user1@example.com",
                    "date": "2023-06-15",
                },
                {
                    "participant": "user2@gmail.com",
                    "message": "Message from user2@gmail.com",
                    "date": "2023-06-10",
                },
                {
                    "participant": "user3@example.org",
                    "message": "Message from user3@example.org",
                    "date": "2023-06-05",
                },
            ],
            [1],  # Include only second session
            2,  # Header + 1 row
        ),
        # Test case 4: Single session
        (
            [
                {
                    "participant": "support@example.com",
                    "message": "Critical customer issue",
                    "date": "2023-07-20",
                },
            ],
            [0],  # Include the only session
            2,  # Header + 1 row
        ),
        # Test case 5: No sessions included (empty result)
        (
            [
                {
                    "participant": "support@example.com",
                    "message": "Critical customer issue",
                    "date": "2023-07-20",
                },
            ],
            [],  # Include no sessions
            1,  # Header only
        ),
    ],
)
def test_filtered_export_with_mocked_filter(
    mock_get_filtered_sessions, session_configs, filtered_indices, expected_row_count
):
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
            created_at=datetime.datetime.strptime(config["date"], "%Y-%m-%d"),
        )
        sessions.append(session)

    filtered_session_ids = [sessions[i].id for i in filtered_indices]
    mock_queryset = MagicMock()
    mock_queryset.values_list.return_value = filtered_session_ids
    mock_get_filtered_sessions.return_value = mock_queryset

    csv_in_memory = filtered_export_to_csv(experiment, filtered_session_ids)
    csv_content = csv_in_memory.getvalue()
    csv_lines = csv_content.strip().split("\n") if csv_content.strip() else []
    assert len(csv_lines) == expected_row_count

    if filtered_indices:
        csv_reader = csv.reader(io.StringIO(csv_content))
        rows = list(csv_reader)[1:]  # Skip header
        assert len(rows) == len(filtered_indices)
        for i in filtered_indices:
            message = session_configs[i]["message"]
            matching_rows = [row for row in rows if message in row]
            assert len(matching_rows) > 0, f"Message for session {i} not found in CSV"
