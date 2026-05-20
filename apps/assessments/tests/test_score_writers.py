import logging
from decimal import Decimal

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.assessments.models import Score
from apps.assessments.score_writers import _score_from_field, write_scores_from_evaluation_result
from apps.utils.factories.evaluations import (
    EvaluationMessageFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluatorFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


@pytest.fixture()
def session_target(db):
    team = TeamFactory.create()
    session = ExperimentSessionFactory.create(team=team, experiment__team=team)
    return team, session


def test_score_from_field_bool_routes_to_numeric_zero_one(session_target):
    team, session = session_target
    s_true = _score_from_field(
        team=team,
        target=session,
        name="flag",
        raw_value=True,
        source=Score.Source.LLM_JUDGE,
        automated_result=None,
    )
    s_false = _score_from_field(
        team=team,
        target=session,
        name="flag",
        raw_value=False,
        source=Score.Source.LLM_JUDGE,
        automated_result=None,
    )
    assert s_true.data_type == Score.DataType.BOOLEAN
    assert s_true.value_numeric == Decimal("1")
    assert s_true.value_string is None
    assert s_false.value_numeric == Decimal("0")
    assert s_false.data_type == Score.DataType.BOOLEAN
    assert s_false.value_string is None


def test_score_from_field_int_and_float_route_to_numeric(session_target):
    team, session = session_target
    s_int = _score_from_field(
        team=team,
        target=session,
        name="rating",
        raw_value=4,
        source=Score.Source.LLM_JUDGE,
        automated_result=None,
    )
    s_float = _score_from_field(
        team=team,
        target=session,
        name="score",
        raw_value=0.83,
        source=Score.Source.LLM_JUDGE,
        automated_result=None,
    )
    assert s_int.data_type == Score.DataType.NUMERIC
    assert s_int.value_numeric == Decimal("4")
    assert s_float.data_type == Score.DataType.NUMERIC
    assert s_float.value_numeric == Decimal("0.83")


def test_score_from_field_str_routes_to_categorical(session_target):
    team, session = session_target
    s = _score_from_field(
        team=team,
        target=session,
        name="label",
        raw_value="positive",
        source=Score.Source.LLM_JUDGE,
        automated_result=None,
    )
    assert s.data_type == Score.DataType.CATEGORICAL
    assert s.value_string == "positive"
    assert s.value_numeric is None


def test_score_from_field_choice_schema_forces_categorical_for_numeric_choices(session_target):
    team, session = session_target
    choice_schema = {"type": "choice", "choices": ["0", "1"], "description": "binary"}
    s = _score_from_field(
        team=team,
        target=session,
        name="binary",
        raw_value="1",
        source=Score.Source.LLM_JUDGE,
        automated_result=None,
        schema_field=choice_schema,
    )
    assert s.data_type == Score.DataType.CATEGORICAL
    assert s.value_string == "1"


def test_score_from_field_unsupported_value_returns_none(session_target, caplog):
    team, session = session_target
    caplog.set_level(logging.WARNING, logger="apps.assessments.score_writers")

    s_none = _score_from_field(
        team=team,
        target=session,
        name="x",
        raw_value=None,
        source=Score.Source.LLM_JUDGE,
        automated_result=None,
    )
    s_list = _score_from_field(
        team=team,
        target=session,
        name="x",
        raw_value=[1, 2],
        source=Score.Source.LLM_JUDGE,
        automated_result=None,
    )
    s_dict = _score_from_field(
        team=team,
        target=session,
        name="x",
        raw_value={"a": 1},
        source=Score.Source.LLM_JUDGE,
        automated_result=None,
    )
    assert s_none is None
    assert s_list is None
    assert s_dict is None
    warnings = [r for r in caplog.records if "unsupported value type" in r.message.lower()]
    assert len(warnings) == 3  # each unsupported call emits one warning


def test_score_from_field_sets_target_gfk_fields(session_target):
    team, session = session_target
    s = _score_from_field(
        team=team,
        target=session,
        name="x",
        raw_value="y",
        source=Score.Source.LLM_JUDGE,
        automated_result=None,
    )
    assert s.target_object_id == session.id
    assert s.target_content_type == ContentType.objects.get_for_model(session)
    assert s.team == team
    assert s.source == Score.Source.LLM_JUDGE


@pytest.fixture()
def eval_result_on_session(db):
    team = TeamFactory.create()
    session = ExperimentSessionFactory.create(team=team, experiment__team=team)
    message = EvaluationMessageFactory.create(session=session)
    evaluator = EvaluatorFactory.create(team=team)
    run = EvaluationRunFactory.create(team=team)
    result = EvaluationResultFactory.create(
        team=team,
        evaluator=evaluator,
        message=message,
        run=run,
        output={
            "result": {
                "sentiment": "positive",
                "score": 7,
                "is_safe": True,
                "weird": [1, 2, 3],
            }
        },
    )
    return team, session, result


@pytest.mark.django_db()
def test_write_scores_from_evaluation_result_decomposes_into_one_score_per_field(eval_result_on_session):
    team, session, result = eval_result_on_session
    write_scores_from_evaluation_result(result)

    scores = {s.name: s for s in Score.objects.filter(automated_result=result)}
    # sentiment maps to schema's choice -> CATEGORICAL
    assert scores["sentiment"].data_type == Score.DataType.CATEGORICAL
    assert scores["sentiment"].value_string == "positive"
    # score is int -> NUMERIC
    assert scores["score"].data_type == Score.DataType.NUMERIC
    assert scores["score"].value_numeric == Decimal("7")
    # is_safe is bool but not in schema -> BOOLEAN
    assert scores["is_safe"].data_type == Score.DataType.BOOLEAN
    assert scores["is_safe"].value_numeric == Decimal("1")
    # weird is a list -> skipped
    assert "weird" not in scores
    # all scores targeted at the session
    session_ct = ContentType.objects.get_for_model(session)
    for s in scores.values():
        assert s.target_content_type == session_ct
        assert s.target_object_id == session.id
        assert s.source == Score.Source.LLM_JUDGE
        assert s.team == team


@pytest.mark.django_db()
def test_write_scores_from_evaluation_result_with_no_session_is_a_no_op(db):
    team = TeamFactory.create()
    message = EvaluationMessageFactory.create()
    message.session = None
    message.save()
    evaluator = EvaluatorFactory.create(team=team)
    run = EvaluationRunFactory.create(team=team)
    result = EvaluationResultFactory.create(
        team=team,
        evaluator=evaluator,
        message=message,
        run=run,
        output={"result": {"x": "y"}},
    )

    write_scores_from_evaluation_result(result)

    assert Score.objects.filter(automated_result=result).count() == 0


@pytest.mark.django_db()
def test_write_scores_from_evaluation_result_is_idempotent(eval_result_on_session):
    _, _, result = eval_result_on_session
    write_scores_from_evaluation_result(result)
    first_ids = set(Score.objects.filter(automated_result=result).values_list("id", flat=True))

    write_scores_from_evaluation_result(result)
    second_ids = set(Score.objects.filter(automated_result=result).values_list("id", flat=True))

    # delete-and-recreate: counts match, IDs differ
    assert len(first_ids) == len(second_ids)
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.django_db()
def test_write_scores_from_evaluation_result_error_payload_writes_no_scores(eval_result_on_session):
    team, session, result = eval_result_on_session
    result.output = {"error": "boom"}
    result.save()

    write_scores_from_evaluation_result(result)
    assert Score.objects.filter(automated_result=result).count() == 0
