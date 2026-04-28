"""Pure unit tests for apps.evaluations.tagging — no DB access."""

from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError

from apps.evaluations.field_definitions import (
    ChoiceFieldDefinition,
    FloatFieldDefinition,
    IntFieldDefinition,
    StringFieldDefinition,
)
from apps.evaluations.rule_validation import validate_condition, validate_field_in_schema
from apps.evaluations.tagging import evaluate_rules, matches, resolve_target


class _Rule:
    """Lightweight stand-in for EvaluatorTagRule for non-DB tests."""

    def __init__(self, pk, field_name, condition_type, condition_value):
        self.pk = pk
        self.field_name = field_name
        self.condition_type = condition_type
        self.condition_value = condition_value


# ---- matches ---------------------------------------------------------------


class TestMatches:
    @pytest.mark.parametrize(
        ("condition_type", "condition_value", "field_value", "expected"),
        [
            ("equals", {"value": "positive"}, "positive", True),
            ("equals", {"value": "positive"}, "negative", False),
            ("range", {"min": 1, "max": 5}, 1, True),
            ("range", {"min": 1, "max": 5}, 5, True),
            ("range", {"min": 1, "max": 5}, 0, False),
            ("range", {"min": 1, "max": 5}, 6, False),
            ("range", {"min": 0.0, "max": 1.0}, 0.5, True),
            ("range", {"min": 0.0, "max": 1.0}, "0.5", True),
            ("range", {"min": 0, "max": 10}, "not-a-number", False),
        ],
    )
    def test_matches(self, condition_type, condition_value, field_value, expected):
        assert matches(condition_type, condition_value, field_value) is expected

    def test_unknown_condition_raises(self):
        with pytest.raises(ValueError, match="Unknown condition type"):
            matches("contains", {"value": "x"}, "x")


# ---- validate_condition ----------------------------------------------------


class TestValidateCondition:
    @pytest.mark.parametrize(
        ("condition_type", "condition_value", "field"),
        [
            ("equals", {"value": "a"}, ChoiceFieldDefinition(type="choice", description="d", choices=["a", "b"])),
            ("equals", {"value": 1}, IntFieldDefinition(type="int", description="d")),
            ("equals", {"value": 1.5}, FloatFieldDefinition(type="float", description="d")),
            ("equals", {"value": "hello"}, StringFieldDefinition(type="string", description="d")),
            ("range", {"min": 1, "max": 10}, IntFieldDefinition(type="int", description="d")),
            ("range", {"min": 0.0, "max": 1.0}, FloatFieldDefinition(type="float", description="d")),
        ],
    )
    def test_valid(self, condition_type, condition_value, field):
        validate_condition(condition_type, condition_value, field)

    @pytest.mark.parametrize(
        ("condition_type", "condition_value", "field"),
        [
            (
                "equals",
                {"value": "c"},
                ChoiceFieldDefinition(type="choice", description="d", choices=["a", "b"]),
            ),
            ("equals", {"value": "abc"}, IntFieldDefinition(type="int", description="d")),
            (
                "equals",
                {"value": "a", "extra": 1},
                ChoiceFieldDefinition(type="choice", description="d", choices=["a"]),
            ),
            ("equals", {}, ChoiceFieldDefinition(type="choice", description="d", choices=["a"])),
            ("range", {"min": 5, "max": 1}, IntFieldDefinition(type="int", description="d")),
            ("range", {"min": 1, "max": 2}, StringFieldDefinition(type="string", description="d")),
            ("range", {"min": 1}, IntFieldDefinition(type="int", description="d")),
            ("range", {"min": 1, "max": 10, "nope": 0}, IntFieldDefinition(type="int", description="d")),
            ("range", [1, 10], IntFieldDefinition(type="int", description="d")),
            ("contains", {}, IntFieldDefinition(type="int", description="d")),
        ],
    )
    def test_rejected(self, condition_type, condition_value, field):
        with pytest.raises(ValidationError):
            validate_condition(condition_type, condition_value, field)


# ---- validate_field_in_schema ---------------------------------------------


class TestValidateFieldInSchema:
    def test_present(self):
        schema = {"score": {"type": "int", "description": "d"}}
        result = validate_field_in_schema("score", schema)
        assert isinstance(result, IntFieldDefinition)

    def test_missing(self):
        with pytest.raises(ValidationError):
            validate_field_in_schema("missing", {"score": {"type": "int", "description": "d"}})

    def test_empty_field_name(self):
        with pytest.raises(ValidationError):
            validate_field_in_schema("", {"x": {"type": "int", "description": "d"}})


# ---- evaluate_rules --------------------------------------------------------


class TestEvaluateRules:
    def test_empty_list(self):
        assert evaluate_rules([], {"result": {"sentiment": "positive"}}) == []

    def test_mixed_matches(self):
        r1 = _Rule(1, "sentiment", "equals", {"value": "positive"})
        r2 = _Rule(2, "sentiment", "equals", {"value": "negative"})
        r3 = _Rule(3, "score", "range", {"min": 1, "max": 5})
        result = {"result": {"sentiment": "positive", "score": 4}}
        matched = evaluate_rules([r1, r2, r3], result)
        assert matched == [r1, r3]

    def test_missing_field_skipped(self, caplog):
        r = _Rule(1, "missing", "equals", {"value": "x"})
        assert evaluate_rules([r], {"result": {"sentiment": "positive"}}) == []
        assert any("not present" in rec.message for rec in caplog.records)

    def test_type_mismatch_skipped(self):
        r = _Rule(1, "score", "range", {"min": 1, "max": 5})
        # Non-numeric value for a range condition — matches returns False (no raise)
        assert evaluate_rules([r], {"result": {"score": "abc"}}) == []

    def test_none_output(self):
        r = _Rule(1, "sentiment", "equals", {"value": "x"})
        assert evaluate_rules([r], None) == []

    def test_output_without_result_key(self):
        r = _Rule(1, "sentiment", "equals", {"value": "x"})
        assert evaluate_rules([r], {"error": "boom"}) == []


# ---- resolve_target --------------------------------------------------------


class TestResolveTarget:
    def test_session_mode_with_session(self):
        evaluator = MagicMock()
        evaluator.evaluation_mode = "session"
        message = MagicMock()
        chat = MagicMock()
        message.session.chat = chat
        assert resolve_target(evaluator, message) is chat

    def test_session_mode_no_session(self):
        evaluator = MagicMock()
        evaluator.evaluation_mode = "session"
        message = MagicMock()
        message.session = None
        assert resolve_target(evaluator, message) is None

    def test_message_mode_with_chat_message(self):
        evaluator = MagicMock()
        evaluator.evaluation_mode = "message"
        message = MagicMock()
        chat_message = MagicMock()
        message.expected_output_chat_message = chat_message
        assert resolve_target(evaluator, message) is chat_message
