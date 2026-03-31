import pytest

from apps.evaluations.models import EvaluationMessage


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
