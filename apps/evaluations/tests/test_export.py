"""Tests for the evaluations export/table helpers (apps/evaluations/export.py)."""

import pytest

from apps.evaluations.export import (
    CategoricalColumn,
    CategoricalValue,
    categorical_columns_for_evaluators,
    evaluator_output_columns,
)
from apps.utils.factories.evaluations import EvaluatorFactory


@pytest.mark.django_db()
def test_categorical_columns_for_evaluators_includes_choice_fields():
    """The default factory schema has one "choice" field - `sentiment` - so it should
    surface as one CategoricalColumn keyed by the composite string build_evaluation_table_data
    uses for that column."""
    evaluator = EvaluatorFactory.create(name="Sentiment Judge")

    columns = categorical_columns_for_evaluators([evaluator])

    assert columns == [
        CategoricalColumn(
            column_key="sentiment (Sentiment Judge)",
            field_label="Sentiment",
            values=[
                CategoricalValue(raw="positive", label="positive"),
                CategoricalValue(raw="neutral", label="neutral"),
                CategoricalValue(raw="negative", label="negative"),
            ],
        )
    ]


@pytest.mark.django_db()
def test_categorical_columns_for_evaluators_colors_a_two_choice_field():
    """A two-choice field is the one case with a real, generic positive/negative signal:
    the first-listed choice is "positive", the second "negative" - so the results table
    can color-code it (e.g. green Acceptable / red Unacceptable)."""
    evaluator = EvaluatorFactory.create(
        name="Acceptability Judge",
        params={
            "llm_prompt": "x",
            "output_schema": {
                "acceptability": {
                    "type": "choice",
                    "description": "x",
                    "choices": ["Acceptable", "Unacceptable"],
                },
            },
        },
    )

    columns = categorical_columns_for_evaluators([evaluator])

    assert columns == [
        CategoricalColumn(
            column_key="acceptability (Acceptability Judge)",
            field_label="Acceptability",
            values=[
                CategoricalValue(raw="Acceptable", label="Acceptable", polarity="positive"),
                CategoricalValue(raw="Unacceptable", label="Unacceptable", polarity="negative"),
            ],
        )
    ]


@pytest.mark.django_db()
def test_categorical_columns_for_evaluators_includes_binary_fields_as_1_0():
    """Binary fields are stored as 1/0 (see BinaryFieldDefinition.python_type), so the
    column's raw values are the strings "1"/"0", not the true/false labels. Like a
    two-choice field, the true value is treated as positive and false as negative -
    right for a field like "correct" (true is good), even though the direction isn't
    universal (true is bad for a field like "suspected_ai_usage")."""
    evaluator = EvaluatorFactory.create(name="Correctness Judge", binary_schema=True)

    columns = categorical_columns_for_evaluators([evaluator])

    assert columns == [
        CategoricalColumn(
            column_key="correct (Correctness Judge)",
            field_label="Correct",
            values=[
                CategoricalValue(raw="1", label="Correct", polarity="positive"),
                CategoricalValue(raw="0", label="Incorrect", polarity="negative"),
            ],
        )
    ]


@pytest.mark.django_db()
def test_categorical_columns_for_evaluators_skips_evaluators_with_no_output_schema():
    """A Python evaluator's params carry `code`, not `output_schema` - it contributes no
    filterable/badge-able columns."""
    evaluator = EvaluatorFactory.create(type="PythonEvaluator", params={"code": "def main(**kwargs): return {}"})

    assert categorical_columns_for_evaluators([evaluator]) == []


@pytest.mark.django_db()
def test_categorical_columns_for_evaluators_skips_string_and_numeric_fields():
    evaluator = EvaluatorFactory.create(
        params={
            "llm_prompt": "x",
            "output_schema": {
                "notes": {"type": "string", "description": "free text"},
                "score": {"type": "int", "description": "a score"},
            },
        }
    )

    assert categorical_columns_for_evaluators([evaluator]) == []


@pytest.mark.django_db()
def test_evaluator_output_columns_covers_every_field_type_with_bare_labels():
    """Unlike categorical_columns_for_evaluators, this includes non-categorical fields
    too (the results table shows a column for every output field), and drops the
    evaluator-name suffix from the label - one column per field, not per (evaluator,
    field) pair."""
    evaluator = EvaluatorFactory.create(
        name="Acceptability Judge",
        params={
            "llm_prompt": "x",
            "output_schema": {
                "acceptability": {"type": "choice", "description": "x", "choices": ["Acceptable", "Unacceptable"]},
                "suspected_ai": {"type": "binary", "description": "x", "true_label": "Yes", "false_label": "No"},
                "notes": {"type": "string", "description": "free text"},
            },
        },
    )

    assert evaluator_output_columns([evaluator]) == [
        ("acceptability (Acceptability Judge)", "Acceptability"),
        ("suspected_ai (Acceptability Judge)", "Suspected AI"),
        ("notes (Acceptability Judge)", "Notes"),
    ]
