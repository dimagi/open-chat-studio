import datetime
import json

import pytest

from apps.annotations.models import Tag
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.export import filtered_export_to_csv, get_filtered_sessions
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("session_configs", "filters", "expected_chats_count"),
    [
        # Basic tag filtering
        (
            [
                {
                    "tags": ["session1"],
                    "participant": "user1@example.com",
                    "last_message": "2023-06-15",
                    "versions": "v1",
                },
                {
                    "tags": ["session2"],
                    "participant": "user2@gmail.com",
                    "last_message": "2023-06-10",
                    "versions": "v1",
                },
                {"tags": [], "participant": "user3@example.org", "last_message": "2023-06-05", "versions": "v1"},
            ],
            [{"column": "tags", "operator": "any of", "value": ["session1"]}],
            1,
        ),
        # Participant filtering - contains
        (
            [
                {
                    "tags": ["session1"],
                    "participant": "user1@example.com",
                    "last_message": "2023-06-15",
                    "versions": "v1",
                },
                {
                    "tags": ["session2"],
                    "participant": "user2@gmail.com",
                    "last_message": "2023-06-10",
                    "versions": "v1",
                },
                {"tags": [], "participant": "user3@example.org", "last_message": "2023-06-05", "versions": "v1"},
            ],
            [{"column": "participant", "operator": "contains", "value": "gmail"}],
            1,
        ),
        # Participant filtering - equals
        (
            [
                {
                    "tags": ["session1"],
                    "participant": "user1@example.com",
                    "last_message": "2023-06-15",
                    "versions": ["v1"],
                },
                {
                    "tags": ["session2"],
                    "participant": "user2@gmail.com",
                    "last_message": "2023-06-10",
                    "versions": ["v1"],
                },
                {"tags": [], "participant": "user3@example.org", "last_message": "2023-06-05", "versions": ["v1"]},
            ],
            [{"column": "participant", "operator": "equals", "value": "user1@example.com"}],
            1,
        ),
        # Multiple filters combined
        (
            [
                {
                    "tags": ["important", "review"],
                    "participant": "user1@example.com",
                    "last_message": "2023-06-15",
                    "versions": ["v1"],
                },
                {
                    "tags": ["urgent", "review"],
                    "participant": "user2@gmail.com",
                    "last_message": "2023-06-10",
                    "versions": ["v1"],
                },
                {
                    "tags": ["normal"],
                    "participant": "user3@example.org",
                    "last_message": "2023-06-05",
                    "versions": ["v1"],
                },
            ],
            [
                {"column": "tags", "operator": "any of", "value": ["important", "urgent"]},
                {"column": "participant", "operator": "contains", "value": "example"},
                {"column": "last_message", "operator": "after", "value": "2023-06-01"},
            ],
            1,
        ),
        # Tag filtering - all of
        (
            [
                {
                    "tags": ["important", "review"],
                    "participant": "user1@example.com",
                    "last_message": "2023-06-15",
                    "versions": ["v1"],
                },
                {
                    "tags": ["urgent", "review"],
                    "participant": "user2@gmail.com",
                    "last_message": "2023-06-10",
                    "versions": ["v2"],
                },
                {
                    "tags": ["normal"],
                    "participant": "user3@example.org",
                    "last_message": "2023-06-05",
                    "versions": ["v1"],
                },
            ],
            [{"column": "tags", "operator": "all of", "value": ["important", "review"]}],
            1,
        ),
        # No matches
        (
            [
                {
                    "tags": ["session1"],
                    "participant": "user1@example.com",
                    "last_message": "2023-06-15",
                    "versions": ["v1"],
                },
                {
                    "tags": ["session2"],
                    "participant": "user2@gmail.com",
                    "last_message": "2023-06-10",
                    "versions": ["v2"],
                },
                {"tags": [], "participant": "user3@example.org", "last_message": "2023-06-05", "versions": ["v1"]},
            ],
            [{"column": "participant", "operator": "equals", "value": "no one"}],
            0,
        ),
        # No filters (returns all)
        (
            [
                {
                    "tags": ["session1"],
                    "participant": "user1@example.com",
                    "last_message": "2023-06-15",
                    "versions": ["v1"],
                },
                {
                    "tags": ["session2"],
                    "participant": "user2@gmail.com",
                    "last_message": "2023-06-10",
                    "versions": ["v2"],
                },
                {"tags": [], "participant": "user3@example.org", "last_message": "2023-06-05", "versions": ["v1"]},
            ],
            [],
            3,
        ),
    ],
)
def test_filtered_export_with_multiple_sessions(session_configs, filters, expected_chats_count):
    experiment = ExperimentFactory()
    user = experiment.owner
    team = experiment.team

    all_possible_tags = set()
    for session_config in session_configs:
        all_possible_tags.update(session_config.get("tags", []))

    for filter_item in filters:
        if filter_item.get("column") == "tags" and isinstance(filter_item.get("value"), list):
            all_possible_tags.update(filter_item["value"])

    for name in all_possible_tags:
        if name:
            Tag.objects.create(name=name, slug=name, team=team, created_by=user)
    sessions = []
    for session_config in session_configs:
        session = ExperimentSessionFactory(
            experiment=experiment,
            team=team,
            experiment_channel=ExperimentChannelFactory(),
            participant__identifier=session_config.get("participant", "user@example.com"),
        )
        if session_config.get("tags"):
            session.chat.add_tags(session_config["tags"], team=team, added_by=user)

        date_str = session_config.get("last_message", "2023-06-01")
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        print(date_str)

        ChatMessage.objects.create(
            chat=session.chat,
            content=f"Message from {session.participant.identifier}",
            message_type=ChatMessageType.HUMAN,
            created_at=date_obj,
        )
        sessions.append(session)

    filter_params = {}
    for i, filter_item in enumerate(filters):
        filter_params[f"filter_{i}_column"] = filter_item["column"]
        filter_params[f"filter_{i}_operator"] = filter_item["operator"]
        if isinstance(filter_item["value"], list):
            filter_params[f"filter_{i}_value"] = json.dumps(filter_item["value"])
        else:
            filter_params[f"filter_{i}_value"] = filter_item["value"]

    filtered_sessions = get_filtered_sessions(experiment, filter_params)
    assert filtered_sessions.count() == expected_chats_count

    if expected_chats_count > 0:
        session_ids = list(filtered_sessions.values_list("id", flat=True))
        csv_in_memory = filtered_export_to_csv(experiment, session_ids)

        csv_content = csv_in_memory.getvalue()
        csv_lines = csv_content.strip().split("\n")
        assert len(csv_lines) == expected_chats_count + 1
