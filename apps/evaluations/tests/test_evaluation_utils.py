import pytest

from apps.chat.models import ChatMessageType
from apps.evaluations.exceptions import HistoryParseException
from apps.evaluations.utils import make_evaluation_messages_from_sessions, parse_history_text
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory


def test_parse_history_functionality():
    """Test the history parsing functionality."""

    # Test empty history
    assert parse_history_text("") == []

    # Test single line history
    history_text = "user: Hello there"
    result = parse_history_text(history_text)
    assert len(result) == 1
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "Hello there"

    # Test multi-line history
    history_text = "user: Hello\nassistant: Hi there!\nuser: How are you?"
    result = parse_history_text(history_text)
    assert len(result) == 3
    assert result[0]["message_type"] == "human"
    assert result[1]["message_type"] == "ai"
    assert result[2]["message_type"] == "human"

    # Test message with newlines in content
    history_text = "user: This is a multi-line\nmessage with newlines\nassistant: I understand your\nmulti-line message"
    result = parse_history_text(history_text)
    assert len(result) == 2
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "This is a multi-line\nmessage with newlines"
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "I understand your\nmulti-line message"

    # Test messages with continuation lines (valid format)
    history_text = "user: Hello\nsome random text without role\nassistant: Hi there!\nmore garbled content"
    result = parse_history_text(history_text)
    assert len(result) == 2  # Only the valid human/ai messages should be parsed
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "Hello\nsome random text without role"  # Continuation line included
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "Hi there!\nmore garbled content"  # Continuation line included

    # Test different casings (HUMAN, Human, AI, Ai, etc.)
    history_text = "USER: Hello from uppercase\nAssistant: Mixed case response\nuser: lowercase again"
    result = parse_history_text(history_text)
    assert len(result) == 3
    assert result[0]["message_type"] == "human"  # Always normalized to lowercase
    assert result[0]["content"] == "Hello from uppercase"
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "Mixed case response"
    assert result[2]["message_type"] == "human"
    assert result[2]["content"] == "lowercase again"

    # Test validation: history text that doesn't start with user: or assistant: raises exception
    invalid_history_text = "This is just random text\nuser: without proper formatting"
    with pytest.raises(HistoryParseException):
        parse_history_text(invalid_history_text)

    # Test validation: history text starting with other roles raises exception
    invalid_history_text2 = "system: This is a system message\nuser: Hello"
    with pytest.raises(HistoryParseException):
        parse_history_text(invalid_history_text2)

    # Test validation: empty lines and whitespace should still work if first line is valid
    history_text_with_whitespace = "\n\n  user: Hello with whitespace  \n\n  assistant: Response  \n\n"
    result = parse_history_text(history_text_with_whitespace)
    assert len(result) == 2
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "Hello with whitespace"
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "Response"


@pytest.mark.django_db()
def test_make_evaluation_messages_from_sessions():
    """Test the make_evaluation_messages_from_sessions function with various message configurations."""
    # Setup
    team = TeamFactory()
    session = ExperimentSessionFactory(team=team)
    chat = session.chat

    # 1. Human message + AI message pair
    human_msg_1 = ChatMessageFactory(
        chat=chat,
        message_type=ChatMessageType.HUMAN,
        content="First human message",
    )
    ai_msg_1 = ChatMessageFactory(
        chat=chat,
        message_type=ChatMessageType.AI,
        content="First AI response",
    )

    # 2. Human message without AI message (orphaned human)
    human_msg_2 = ChatMessageFactory(
        chat=chat,
        message_type=ChatMessageType.HUMAN,
        content="Second human message without response",
    )

    # 3. Another human + AI message pair (so there are two human messages next to each other)
    # This human message will have a trace with participant and session data
    human_msg_3 = ChatMessageFactory(
        chat=chat,
        message_type=ChatMessageType.HUMAN,
        content="Third human message",
    )
    # Let's add a trace with participant and session data
    TraceFactory(
        team=team,
        experiment=session.experiment,
        session=session,
        participant=session.participant,
        input_message=human_msg_3,
        duration=100,
        participant_data={"name": "John Doe", "age": 30, "location": "New York"},
        session_state={"current_step": 3, "total_interactions": 5, "session_score": 85},
    )

    ai_msg_3 = ChatMessageFactory(
        chat=chat,
        message_type=ChatMessageType.AI,
        content="Third AI response",
    )

    # 4. AI message without human message (orphaned AI)
    ai_msg_4 = ChatMessageFactory(
        chat=chat,
        message_type=ChatMessageType.AI,
        content="Fourth AI message without human input",
    )

    # Execute
    message_ids_per_session = {
        session.external_id: [
            human_msg_1.id,
            ai_msg_1.id,
            human_msg_2.id,
            human_msg_3.id,
            ai_msg_3.id,
            ai_msg_4.id,
        ]
    }

    result = make_evaluation_messages_from_sessions(message_ids_per_session)

    # Assertions
    # We should have 4 evaluation messages created
    assert len(result) == 4

    # Test message 1: Human + AI pair
    eval_msg_1 = result[0]
    assert eval_msg_1.input_chat_message == human_msg_1
    assert eval_msg_1.expected_output_chat_message == ai_msg_1
    assert eval_msg_1.input["content"] == "First human message"
    assert eval_msg_1.input["role"] == "human"
    assert eval_msg_1.output["content"] == "First AI response"
    assert eval_msg_1.output["role"] == "ai"
    assert eval_msg_1.metadata["session_id"] == session.external_id
    assert eval_msg_1.metadata["experiment_id"] == str(session.experiment.public_id)
    assert eval_msg_1.history == []  # First message, no history
    assert eval_msg_1.participant_data == {}  # No trace data
    assert eval_msg_1.session_state == {}  # No trace data

    # Test message 2: Orphaned human message
    eval_msg_2 = result[1]
    assert eval_msg_2.input_chat_message == human_msg_2
    assert eval_msg_2.expected_output_chat_message is None
    assert eval_msg_2.input["content"] == "Second human message without response"
    assert eval_msg_2.input["role"] == "human"
    assert eval_msg_2.output == {}  # No output for orphaned human
    assert eval_msg_2.metadata["session_id"] == session.external_id
    assert len(eval_msg_2.history) == 2  # Previous human + AI pair should be in history
    assert eval_msg_2.history[0]["message_type"] == ChatMessageType.HUMAN
    assert eval_msg_2.history[0]["content"] == "First human message"
    assert eval_msg_2.history[1]["message_type"] == ChatMessageType.AI
    assert eval_msg_2.history[1]["content"] == "First AI response"

    # Test message 3: Human + AI pair with trace data
    eval_msg_3 = result[2]
    assert eval_msg_3.input_chat_message == human_msg_3
    assert eval_msg_3.expected_output_chat_message == ai_msg_3
    assert eval_msg_3.input["content"] == "Third human message"
    assert eval_msg_3.input["role"] == "human"
    assert eval_msg_3.output["content"] == "Third AI response"
    assert eval_msg_3.output["role"] == "ai"
    assert eval_msg_3.metadata["session_id"] == session.external_id
    # Verify participant and session data from trace is transferred
    assert eval_msg_3.participant_data == {"name": "John Doe", "age": 30, "location": "New York"}
    assert eval_msg_3.session_state == {"current_step": 3, "total_interactions": 5, "session_score": 85}
    # History should include previous messages
    assert len(eval_msg_3.history) == 3  # human1, ai1, human2
    assert eval_msg_3.history[0]["message_type"] == ChatMessageType.HUMAN
    assert eval_msg_3.history[1]["message_type"] == ChatMessageType.AI
    assert eval_msg_3.history[2]["message_type"] == ChatMessageType.HUMAN

    # Test message 4: Orphaned AI message
    eval_msg_4 = result[3]
    assert eval_msg_4.input_chat_message is None
    assert eval_msg_4.expected_output_chat_message == ai_msg_4
    assert eval_msg_4.input == {}  # No input for orphaned AI
    assert eval_msg_4.output["content"] == "Fourth AI message without human input"
    assert eval_msg_4.output["role"] == "ai"
    assert eval_msg_4.metadata["session_id"] == session.external_id
    # History should include all previous messages
    assert len(eval_msg_4.history) == 5  # human1, ai1, human2, human3, ai3
