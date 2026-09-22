import pickle

import pytest

from apps.chat.exceptions import (
    ChatException,
    EmptyModelResponseError,
    ModelRefusedTurnError,
    UserActionableError,
)


class TestModelRefusedTurnError:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            pytest.param("refusal", "The assistant declined to answer the last message.", id="refusal"),
            pytest.param(
                "content_filter", "The last message was blocked by the provider's content filter.", id="filter"
            ),
        ],
    )
    def test_message_is_written_for_the_participant(self, kind, expected):
        error = ModelRefusedTurnError(kind, provider_reason="x", detail={"category": "hate"})

        assert str(error) == expected
        assert error.message == expected

    def test_is_participant_actionable(self):
        assert isinstance(ModelRefusedTurnError("refusal"), UserActionableError)

    def test_trace_metadata_carries_the_signal_and_detail(self):
        error = ModelRefusedTurnError("content_filter", "SAFETY", {"safety_ratings": [{"blocked": True}]})

        assert error.trace_metadata == {
            "model_turn_outcome": "content_filter",
            "provider_reason": "SAFETY",
            "detail": {"safety_ratings": [{"blocked": True}]},
        }

    def test_message_metadata_omits_detail(self):
        error = ModelRefusedTurnError("refusal", "refusal", {"stop_details": {"category": "x"}})

        assert error.message_metadata == {"kind": "refusal", "provider_reason": "refusal"}

    def test_rebuilds_from_args(self):
        """Celery result backends reconstruct exceptions from ``args``."""
        error = ModelRefusedTurnError("content_filter", "content_filter", {"k": "v"})

        rebuilt = pickle.loads(pickle.dumps(error))

        assert rebuilt.kind == "content_filter"
        assert rebuilt.provider_reason == "content_filter"
        assert rebuilt.detail == {"k": "v"}
        assert str(rebuilt) == str(error)


class TestEmptyModelResponseError:
    def test_is_not_a_chat_exception(self):
        """The catch-all's generic prompt applies; the ChatException prompt asks the participant to adjust."""
        assert not isinstance(EmptyModelResponseError(), ChatException)

    def test_text_names_the_stop_reason(self):
        assert str(EmptyModelResponseError("OTHER")) == "The model returned an empty response (stop reason: OTHER)"
        assert str(EmptyModelResponseError()) == "The model returned an empty response (stop reason: none)"

    def test_rebuilds_from_args(self):
        rebuilt = pickle.loads(pickle.dumps(EmptyModelResponseError("OTHER")))

        assert rebuilt.provider_reason == "OTHER"
