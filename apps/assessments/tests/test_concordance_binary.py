import pytest

from apps.assessments.models import Score
from apps.assessments.views import _candidate_categorical_fields, _score_compare_value, _score_value
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluatorFactory
from apps.utils.factories.human_annotations import AnnotationQueueFactory


@pytest.mark.django_db()
def test_binary_fields_on_both_sides_are_concordance_candidates():
    evaluator = EvaluatorFactory(binary_schema=True)
    config = EvaluationConfigFactory(team=evaluator.team, evaluators=[evaluator])
    queue = AnnotationQueueFactory(team=evaluator.team, binary_schema=True)

    assert _candidate_categorical_fields(config, queue) == ["correct"]


@pytest.mark.django_db()
def test_choice_binary_type_mismatch_is_not_a_candidate():
    evaluator = EvaluatorFactory(binary_schema=True)
    config = EvaluationConfigFactory(team=evaluator.team, evaluators=[evaluator])
    queue = AnnotationQueueFactory(
        team=evaluator.team,
        schema={"correct": {"type": "choice", "description": "d", "choices": ["yes", "no"]}},
    )

    assert _candidate_categorical_fields(config, queue) == []


def test_boolean_score_display_prefers_label_and_comparison_uses_boolean():
    labelled = Score(data_type=Score.DataType.BOOLEAN, value_numeric=1, value_string="Correct")
    unlabelled = Score(data_type=Score.DataType.BOOLEAN, value_numeric=1, value_string=None)

    assert _score_value(labelled) == "Correct"
    assert _score_value(unlabelled) is True
    assert _score_compare_value(labelled) is True
    assert _score_compare_value(unlabelled) is True
