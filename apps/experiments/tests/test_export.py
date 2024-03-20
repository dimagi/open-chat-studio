import pytest

from apps.annotations.models import Tag
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.export import experiment_to_message_export_rows
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory


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

    for name in set(chat1_tags + chat2_tags):
        Tag.objects.create(name=name, slug=name, team=team, created_by=user)

    session_2 = ExperimentSessionFactory(
        experiment=session_1.experiment, team=session_1.team, experiment_channel=ExperimentChannelFactory()
    )
    session_3 = ExperimentSessionFactory(
        experiment=session_1.experiment, team=session_1.team, experiment_channel=ExperimentChannelFactory()
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
    assert len(rows) == expected_chats_count
