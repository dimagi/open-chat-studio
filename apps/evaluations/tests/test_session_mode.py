import pytest

from apps.chat.models import ChatMessageType
from apps.evaluations.models import EvaluationMessage
from apps.evaluations.utils import make_session_evaluation_messages
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory


@pytest.mark.django_db()
class TestSessionModeEvaluationMessage:
    def test_str_with_empty_input_output(self):
        """Session-mode messages have empty input/output dicts."""
        msg = EvaluationMessage(
            input={},
            output={},
            history=[
                {"message_type": "human", "content": "Hello there"},
                {"message_type": "ai", "content": "Hi!"},
            ],
        )
        result = str(msg)
        assert result == "Session evaluation"

    def test_str_with_normal_input_output(self):
        """Message-mode messages should still work as before."""
        msg = EvaluationMessage(
            input={"content": "Hello", "role": "human"},
            output={"content": "Hi!", "role": "ai"},
        )
        result = str(msg)
        assert "Hello" in result
        assert "Hi!" in result

    def test_as_result_dict_includes_participant_data_and_session_state(self):
        """as_result_dict should include participant_data and session_state."""
        msg = EvaluationMessage(
            input={"content": "Hello", "role": "human"},
            output={"content": "Hi!", "role": "ai"},
            context={"current_datetime": "2025-01-01"},
            history=[],
            metadata={"session_id": "abc"},
            participant_data={"name": "John"},
            session_state={"step": 1},
        )
        result = msg.as_result_dict()
        assert result["participant_data"] == {"name": "John"}
        assert result["session_state"] == {"step": 1}
        assert result["input"] == {"content": "Hello", "role": "human"}
        assert result["output"] == {"content": "Hi!", "role": "ai"}
        assert result["context"] == {"current_datetime": "2025-01-01"}
        assert result["history"] == []
        assert result["metadata"] == {"session_id": "abc"}


@pytest.mark.django_db()
class TestMakeSessionEvaluationMessages:
    def test_happy_path_multi_turn_session(self):
        """Session with N turns produces one EvaluationMessage with full history."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ai_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi there!")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="How are you?")

        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=ai_1,
            duration=100,
            participant_data={"name": "Alice"},
            session_state={"step": 2},
        )

        ai_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="I'm doing well!")
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=ai_2,
            duration=100,
            participant_data={"name": "Alice", "visits": 3},
            session_state={"step": 3},
        )

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        msg = result[0]
        assert msg.input == {}
        assert msg.output == {}
        assert len(msg.history) == 4
        assert msg.history[0]["message_type"] == ChatMessageType.HUMAN
        assert msg.history[0]["content"] == "Hello"
        assert msg.history[1]["message_type"] == ChatMessageType.AI
        assert msg.history[1]["content"] == "Hi there!"
        assert msg.history[2]["message_type"] == ChatMessageType.HUMAN
        assert msg.history[2]["content"] == "How are you?"
        assert msg.history[3]["message_type"] == ChatMessageType.AI
        assert msg.history[3]["content"] == "I'm doing well!"
        assert msg.participant_data == {"name": "Alice", "visits": 3}
        assert msg.session_state == {"step": 3}
        assert msg.metadata["session_id"] == session.external_id
        assert msg.metadata["experiment_id"] == str(session.experiment.public_id)
        assert msg.metadata["created_mode"] == "clone"
        assert msg.input_chat_message is None
        assert msg.expected_output_chat_message is None

    def test_single_turn_session(self):
        """Session with one human-AI pair still produces one message."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 2

    def test_orphaned_last_human_message(self):
        """Session ending with human message (no AI response)."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Bye")

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 3
        assert result[0].input == {}
        assert result[0].output == {}
        assert result[0].participant_data == {}
        assert result[0].session_state == {}

    def test_human_only_session(self):
        """Session with only human messages."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Anyone there?")

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 2
        assert result[0].participant_data == {}
        assert result[0].session_state == {}

    def test_empty_session(self):
        """Session with no messages produces no EvaluationMessage."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 0

    def test_participant_data_from_last_ai_trace(self):
        """Verify participant_data and session_state come from last AI message's trace."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ai_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=ai_1,
            duration=100,
            participant_data={"version": 1},
            session_state={"first": True},
        )

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="More")
        ai_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Sure!")
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=ai_2,
            duration=100,
            participant_data={"version": 2},
            session_state={"first": False},
        )

        result = make_session_evaluation_messages([session.external_id])

        assert result[0].participant_data == {"version": 2}
        assert result[0].session_state == {"first": False}

    def test_multiple_sessions(self):
        """Multiple sessions produce one EvaluationMessage each."""
        team = TeamFactory.create()
        session_1 = ExperimentSessionFactory.create(team=team)
        session_2 = ExperimentSessionFactory.create(team=team)

        ChatMessageFactory.create(chat=session_1.chat, message_type=ChatMessageType.HUMAN, content="S1 Hello")
        ChatMessageFactory.create(chat=session_1.chat, message_type=ChatMessageType.AI, content="S1 Hi!")
        ChatMessageFactory.create(chat=session_2.chat, message_type=ChatMessageType.HUMAN, content="S2 Hello")
        ChatMessageFactory.create(chat=session_2.chat, message_type=ChatMessageType.AI, content="S2 Hi!")

        result = make_session_evaluation_messages([session_1.external_id, session_2.external_id])

        assert len(result) == 2
        session_ids = {msg.metadata["session_id"] for msg in result}
        assert session_1.external_id in session_ids
        assert session_2.external_id in session_ids

    def test_metadata_structure(self):
        """Verify metadata has session_id, experiment_id, and created_mode."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")

        result = make_session_evaluation_messages([session.external_id])

        metadata = result[0].metadata
        assert metadata["session_id"] == session.external_id
        assert metadata["experiment_id"] == str(session.experiment.public_id)
        assert metadata["created_mode"] == "clone"
