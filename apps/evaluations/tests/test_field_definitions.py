from typing import Literal

import pytest
from pydantic import TypeAdapter, ValidationError

from apps.evaluations.field_definitions import BinaryFieldDefinition, FieldDefinition
from apps.evaluations.utils import schema_to_pydantic_model


class TestBinaryFieldDefinition:
    def test_python_type_is_literal_0_1(self):
        defn = BinaryFieldDefinition(type="binary", description="was the answer correct")
        assert defn.python_type == Literal[0, 1]

    def test_labels_default_to_true_false(self):
        defn = BinaryFieldDefinition(type="binary", description="d")
        assert defn.true_label == "True"
        assert defn.false_label == "False"

    @pytest.mark.parametrize(
        ("true_label", "false_label"),
        [
            pytest.param("", "No", id="empty-true-label"),
            pytest.param("Yes", "", id="empty-false-label"),
            pytest.param("   ", "No", id="whitespace-true-label"),
            pytest.param("Yes", "Yes", id="identical-labels"),
            pytest.param("Yes", " Yes ", id="identical-after-strip"),
        ],
    )
    def test_invalid_labels_rejected(self, true_label, false_label):
        with pytest.raises(ValidationError):
            BinaryFieldDefinition(type="binary", description="d", true_label=true_label, false_label=false_label)

    def test_labels_fold_into_description_not_schema_kwargs(self):
        defn = BinaryFieldDefinition(
            type="binary", description="is it correct", true_label="correct", false_label="incorrect"
        )
        assert defn.pydantic_fields == {"description": "is it correct. 1 = correct, 0 = incorrect"}

    def test_union_discriminates_binary(self):
        parsed = TypeAdapter(FieldDefinition).validate_python(
            {"type": "binary", "description": "d", "true_label": "Yes", "false_label": "No"}
        )
        assert isinstance(parsed, BinaryFieldDefinition)


class TestBinarySchemaToPydanticModel:
    def test_model_accepts_only_0_and_1(self):
        model = schema_to_pydantic_model(
            {"correct": BinaryFieldDefinition(type="binary", description="was it correct")}
        )
        assert model(correct=1).correct == 1
        assert model(correct=0).correct == 0
        with pytest.raises(ValidationError):
            model(correct=2)

    def test_labels_reach_the_judge_via_description(self):
        model = schema_to_pydantic_model(
            {
                "correct": BinaryFieldDefinition(
                    type="binary", description="was it correct", true_label="Right", false_label="Wrong"
                )
            }
        )
        schema = model.model_json_schema()
        assert schema["properties"]["correct"]["description"] == "was it correct. 1 = Right, 0 = Wrong"
        assert "true_label" not in schema["properties"]["correct"]
        assert "false_label" not in schema["properties"]["correct"]
