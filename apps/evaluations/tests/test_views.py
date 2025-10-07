import pytest
from django.urls import reverse

from apps.chat.models import ChatMessageType
from apps.evaluations.models import EvaluationDataset
from apps.trace.models import Trace
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory


@pytest.mark.django_db()
class TestCreateDatasetFromSessionView:
    @pytest.mark.parametrize("create_new_dataset", [True, False])
    def test_create_dataset_with_messages(self, create_new_dataset, client, team_with_users):
        client.force_login(team_with_users.members.first())
        session = ExperimentSessionFactory(team=team_with_users)

        # Simulate human message without an AI response
        h1 = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="hi", chat=session.chat)
        h2 = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="hi again", chat=session.chat)
        ai1 = ChatMessageFactory(message_type=ChatMessageType.AI, content="hi human", chat=session.chat)
        h3 = ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="how are you", chat=session.chat)
        ai2 = ChatMessageFactory(message_type=ChatMessageType.AI, content="I don't have feelings", chat=session.chat)
        ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="Oh ok", chat=session.chat)
        ChatMessageFactory(message_type=ChatMessageType.AI, content="Yup. Kinda sucks", chat=session.chat)

        # Let's add a trace to h2 to test participant_data and session_state copying
        Trace.objects.create(
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            team=team_with_users,
            duration=100,
            input_message=h2,
            output_message=ai1,
            participant_data={"name": "John Doe"},
            session_state={"checkpoint": 1},
        )

        url = reverse(
            "evaluations:create_from_messages",
            args=[team_with_users.slug, session.experiment.public_id, session.external_id],
        )

        if create_new_dataset:
            post_params = {
                "dataset": "",
                "new_dataset_name": "Test Dataset",
                "message_ids": f"{h1.id}, {h2.id}, {h3.id}",
            }
        else:
            dataset = EvaluationDataset.objects.create(name="Existing Dataset", team=team_with_users)
            post_params = {
                "dataset": str(dataset.id),
                "new_dataset_name": "",
                "message_ids": f"{h1.id}, {h2.id}, {h3.id}",
            }

        response = client.post(url, post_params)
        assert response.status_code == 302

        if create_new_dataset:
            dataset = EvaluationDataset.objects.filter(name="Test Dataset", team=team_with_users).first()
        else:
            dataset.refresh_from_db()

        assert dataset is not None, "Dataset should be created"
        assert dataset.messages.count() == 2, "Dataset should contain 2 messages"

        message1 = dataset.messages.filter(input_chat_message_id=h2.id).first()
        assert message1.participant_data == {"name": "John Doe"}
        assert message1.session_state == {"checkpoint": 1}
        assert message1.expected_output_chat_message.id == ai1.id
        assert message1.history == [{"content": "hi", "message_type": "human", "summary": None}]

        message2 = dataset.messages.filter(input_chat_message_id=h3.id).first()
        assert message2.expected_output_chat_message.id == ai2.id
        assert message1.history is not None
