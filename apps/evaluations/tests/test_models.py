import pytest

from apps.chat.models import ChatMessageType
from apps.evaluations.models import EvaluationMessage
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory


@pytest.mark.django_db()
def test_create_messages_from_sessions_includes_history():
    session_1 = ExperimentSessionFactory()
    session_2 = ExperimentSessionFactory(team=session_1.team)

    # Two message pairs from the first session
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="session1 message1 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session1 message1 ai", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="session1 message2 human", chat=session_1.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session1 message2 ai", chat=session_1.chat)

    # One message pair from the second session
    ChatMessageFactory(message_type=ChatMessageType.HUMAN, content="session2 message1 human", chat=session_2.chat)
    ChatMessageFactory(message_type=ChatMessageType.AI, content="session2 message1 ai", chat=session_2.chat)

    eval_messages = EvaluationMessage.create_from_sessions(
        session_1.team, [session_1.external_id, session_2.external_id]
    )

    assert len(eval_messages) == 3

    assert eval_messages[0].input == {"content": "session1 message1 human", "role": "human"}
    assert eval_messages[0].output == {"content": "session1 message1 ai", "role": "ai"}
    assert eval_messages[0].context["history"] == ""

    assert eval_messages[1].input == {"content": "session1 message2 human", "role": "human"}
    assert eval_messages[1].output == {"content": "session1 message2 ai", "role": "ai"}
    assert eval_messages[1].context["history"] == "Human: session1 message1 human\nAI: session1 message1 ai"

    assert eval_messages[2].input == {"content": "session2 message1 human", "role": "human"}
    assert eval_messages[2].output == {"content": "session2 message1 ai", "role": "ai"}
    assert eval_messages[2].context["history"] == ""
