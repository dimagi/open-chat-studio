import pytest

from apps.annotations.models import Tag
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.export import experiment_to_message_export_rows
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("chat1_tags", "chat2_tags", "filter_tags", "expected_chats_count"),
    [
        (["session1"], ["session2"], [], 3),
        (["session1", "test"], ["session2", "test"], ["test"], 2),
        (["session1"], ["session2"], ["session1"], 1),
        (["session1"], ["session2"], ["funny"], 0),
    ],
)
def test_experiment_to_message_export_rows(chat1_tags, chat2_tags, filter_tags, expected_chats_count):
    session_1 = ExperimentSessionFactory(experiment_channel=ExperimentChannelFactory())
    user = session_1.experiment.owner
    team = session_1.team
    experiment = session_1.experiment

    for name in set(chat1_tags + chat2_tags):
        Tag.objects.create(name=name, slug=name, team=team, created_by=user)

    session_2 = ExperimentSessionFactory(
        experiment=experiment, team=team, experiment_channel=ExperimentChannelFactory()
    )
    session_3 = ExperimentSessionFactory(
        experiment=experiment, team=team, experiment_channel=ExperimentChannelFactory()
    )

    # Tag session chats
    session_1.chat.add_tags(chat1_tags, team=team, added_by=user)
    session_2.chat.add_tags(chat2_tags, team=team, added_by=user)

    # We must add a chat message so that something will be returned
    for session in [session_1, session_2, session_3]:
        chat = session.chat
        ChatMessage.objects.create(chat=chat, content="Hi", message_type=ChatMessageType.HUMAN)

    rows = []
    for row in experiment_to_message_export_rows(session_1.experiment, filter_tags):
        rows.append(row)

    rows.pop(0)  # Remove header row
    assert len(rows) == expected_chats_count


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("participants", "expected_message_rows"),
    [
        (None, 3),
        (["alice@gmail.com", "betsie@gmail.com"], 2),
    ],
)
def test_experiment_to_message_export_rows_filters_participants(participants, expected_message_rows):
    experiment = ExperimentFactory()
    session1 = _create_experiment_participant(experiment, participant_id="alice@gmail.com")
    session2 = _create_experiment_participant(experiment, participant_id="john@gmail.com")
    session3 = _create_experiment_participant(experiment, participant_id="betsie@gmail.com")

    for session in [session1, session2, session3]:
        chat = session.chat
        ChatMessage.objects.create(chat=chat, content="Hi", message_type=ChatMessageType.HUMAN)

    rows = []
    for row in experiment_to_message_export_rows(experiment, participants=participants):
        rows.append(row)

    # The extra row is the header row
    assert len(rows) == expected_message_rows + 1


def _create_experiment_participant(experiment, participant_id):
    return ExperimentSessionFactory(
        experiment=experiment,
        participant__identifier=participant_id,
        team=experiment.team,
        experiment_channel=ExperimentChannelFactory(),
    )
