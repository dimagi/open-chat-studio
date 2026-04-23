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
from apps.evaluations.tagging import (
    evaluate_rules,
    matches,
    resolve_target,
    validate_condition,
    validate_field_in_schema,
)


class _Rule:
    """Lightweight stand-in for EvaluatorTagRule for non-DB tests."""

    def __init__(self, pk, field_name, condition_type, condition_value):
        self.pk = pk
        self.field_name = field_name
        self.condition_type = condition_type
        self.condition_value = condition_value


# ---- matches ---------------------------------------------------------------


class TestMatches:
    def test_equals_match(self):
        assert matches("equals", {"value": "positive"}, "positive") is True

    def test_equals_non_match(self):
        assert matches("equals", {"value": "positive"}, "negative") is False

    def test_range_at_min(self):
        assert matches("range", {"min": 1, "max": 5}, 1) is True

    def test_range_at_max(self):
        assert matches("range", {"min": 1, "max": 5}, 5) is True

    def test_range_below(self):
        assert matches("range", {"min": 1, "max": 5}, 0) is False

    def test_range_above(self):
        assert matches("range", {"min": 1, "max": 5}, 6) is False

    def test_range_float_coercion(self):
        assert matches("range", {"min": 0.0, "max": 1.0}, 0.5) is True
        assert matches("range", {"min": 0.0, "max": 1.0}, "0.5") is True

    def test_range_non_numeric_value(self):
        assert matches("range", {"min": 0, "max": 10}, "not-a-number") is False

    def test_unknown_condition_raises(self):
        with pytest.raises(ValueError, match="Unknown condition type"):
            matches("contains", {"value": "x"}, "x")


# ---- validate_condition ----------------------------------------------------


class TestValidateCondition:
    def test_equals_valid(self):
        field = ChoiceFieldDefinition(type="choice", description="d", choices=["a", "b"])
        validate_condition("equals", {"value": "a"}, field)

    def test_equals_invalid_choice(self):
        field = ChoiceFieldDefinition(type="choice", description="d", choices=["a", "b"])
        with pytest.raises(ValidationError):
            validate_condition("equals", {"value": "c"}, field)

    def test_equals_missing_choices_list(self):
        field = ChoiceFieldDefinition(type="choice", description="d", choices=["a"])
        field.choices = []
        with pytest.raises(ValidationError):
            validate_condition("equals", {"value": "a"}, field)

    def test_equals_on_int_field_accepted(self):
        field = IntFieldDefinition(type="int", description="d")
        validate_condition("equals", {"value": 1}, field)

    def test_equals_on_float_field_accepted(self):
        field = FloatFieldDefinition(type="float", description="d")
        validate_condition("equals", {"value": 1.5}, field)

    def test_equals_on_string_field_accepted(self):
        field = StringFieldDefinition(type="string", description="d")
        validate_condition("equals", {"value": "hello"}, field)

    def test_equals_on_int_field_non_numeric_rejected(self):
        field = IntFieldDefinition(type="int", description="d")
        with pytest.raises(ValidationError):
            validate_condition("equals", {"value": "abc"}, field)

    def test_equals_extra_keys_rejected(self):
        field = ChoiceFieldDefinition(type="choice", description="d", choices=["a"])
        with pytest.raises(ValidationError):
            validate_condition("equals", {"value": "a", "extra": 1}, field)

    def test_equals_missing_value_key_rejected(self):
        field = ChoiceFieldDefinition(type="choice", description="d", choices=["a"])
        with pytest.raises(ValidationError):
            validate_condition("equals", {}, field)

    def test_range_valid_int(self):
        field = IntFieldDefinition(type="int", description="d")
        validate_condition("range", {"min": 1, "max": 10}, field)

    def test_range_valid_float(self):
        field = FloatFieldDefinition(type="float", description="d")
        validate_condition("range", {"min": 0.0, "max": 1.0}, field)

    def test_range_min_greater_than_max_rejected(self):
        field = IntFieldDefinition(type="int", description="d")
        with pytest.raises(ValidationError):
            validate_condition("range", {"min": 5, "max": 1}, field)

    def test_range_on_non_numeric_field_rejected(self):
        field = StringFieldDefinition(type="string", description="d")
        with pytest.raises(ValidationError):
            validate_condition("range", {"min": 1, "max": 2}, field)

    def test_range_missing_bounds_rejected(self):
        field = IntFieldDefinition(type="int", description="d")
        with pytest.raises(ValidationError):
            validate_condition("range", {"min": 1}, field)

    def test_range_extra_keys_rejected(self):
        field = IntFieldDefinition(type="int", description="d")
        with pytest.raises(ValidationError):
            validate_condition("range", {"min": 1, "max": 10, "nope": 0}, field)

    def test_non_dict_value_rejected(self):
        field = IntFieldDefinition(type="int", description="d")
        with pytest.raises(ValidationError):
            validate_condition("range", [1, 10], field)

    def test_unknown_condition_type_rejected(self):
        field = IntFieldDefinition(type="int", description="d")
        with pytest.raises(ValidationError):
            validate_condition("contains", {}, field)


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

    def test_message_mode_without_chat_message(self):
        evaluator = MagicMock()
        evaluator.evaluation_mode = "message"
        message = MagicMock()
        message.expected_output_chat_message = None
        assert resolve_target(evaluator, message) is None
