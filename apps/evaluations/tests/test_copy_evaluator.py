"""Tests for Evaluator.copy(): a new active evaluator with the source's configuration and tag rules."""

from unittest.mock import patch

import pytest

from apps.evaluations.models import ConditionType, EvaluationRunStatus, Evaluator, EvaluatorTagRule
from apps.utils.factories.evaluations import (
    EvaluationConfigFactory,
    EvaluationResultFactory,
    EvaluationRunFactory,
    EvaluationTagFactory,
    EvaluatorFactory,
    EvaluatorTagRuleFactory,
)


@pytest.mark.django_db()
def test_copy_carries_the_configuration_and_provider_fks(team_with_users):
    source = EvaluatorFactory.create(team=team_with_users, name="Sentiment scorer", evaluation_mode="session")

    duplicate = source.copy()

    assert duplicate.pk != source.pk
    assert duplicate.team == team_with_users
    assert duplicate.name == "Sentiment scorer (copy)"
    assert duplicate.type == source.type
    assert duplicate.params == source.params
    assert duplicate.evaluation_mode == "session"
    assert duplicate.llm_provider_id == source.llm_provider_id
    assert duplicate.llm_provider_model_id == source.llm_provider_model_id


@pytest.mark.django_db()
def test_copy_clones_the_tag_rules_onto_the_duplicate(team_with_users):
    source = EvaluatorFactory.create(team=team_with_users)
    tag = EvaluationTagFactory.create(team=team_with_users, name="needs-review")
    EvaluatorTagRuleFactory.create(
        team=team_with_users,
        evaluator=source,
        tag=tag,
        field_name="sentiment",
        condition_type=ConditionType.EQUALS,
        condition_value={"value": "negative"},
    )
    EvaluatorTagRuleFactory.create(
        team=team_with_users,
        evaluator=source,
        tag=tag,
        field_name="score",
        condition_type=ConditionType.RANGE,
        condition_value={"min": 1, "max": 3},
    )

    duplicate = source.copy()

    cloned = list(
        duplicate.tag_rules.order_by("field_name").values_list(
            "team_id", "tag_id", "field_name", "condition_type", "condition_value"
        )
    )
    assert cloned == [
        (team_with_users.id, tag.id, "score", ConditionType.RANGE, {"min": 1, "max": 3}),
        (team_with_users.id, tag.id, "sentiment", ConditionType.EQUALS, {"value": "negative"}),
    ]
    assert EvaluatorTagRule.objects.filter(evaluator=source).count() == 2


@pytest.mark.django_db()
def test_copy_leaves_config_membership_and_history_behind(team_with_users):
    source = EvaluatorFactory.create(team=team_with_users)
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[source])
    run = EvaluationRunFactory.create(team=team_with_users, config=config, status=EvaluationRunStatus.COMPLETED)
    EvaluationResultFactory.create(team=team_with_users, run=run, evaluator=source)

    duplicate = source.copy()

    assert list(config.evaluators.all()) == [source]
    assert not duplicate.evaluationconfig_set.exists()
    assert not duplicate.evaluationresult_set.exists()


@pytest.mark.django_db()
def test_copy_of_an_archived_evaluator_is_active_and_the_source_stays_archived(team_with_users):
    source = EvaluatorFactory.create(team=team_with_users)
    source.archive()

    duplicate = source.copy()

    source.refresh_from_db()
    assert duplicate.is_archived is False
    assert source.is_archived is True


@pytest.mark.django_db()
def test_copy_of_an_evaluator_without_a_provider_keeps_both_fks_null(team_with_users):
    source = EvaluatorFactory.create(
        team=team_with_users, type="PythonEvaluator", llm_provider=None, llm_provider_model=None
    )

    duplicate = source.copy()

    assert duplicate.llm_provider_id is None
    assert duplicate.llm_provider_model_id is None
    assert duplicate.type == "PythonEvaluator"


@pytest.mark.django_db()
def test_copy_truncates_a_long_name_so_the_suffix_still_fits(team_with_users):
    source = EvaluatorFactory.create(team=team_with_users, name="x" * 255)

    duplicate = source.copy()

    assert len(duplicate.name) == 255
    assert duplicate.name.endswith(" (copy)")
    assert Evaluator.objects.filter(pk=duplicate.pk).exists()


@pytest.mark.django_db()
def test_copy_leaves_nothing_behind_when_cloning_the_rules_fails(team_with_users):
    source = EvaluatorFactory.create(team=team_with_users)
    EvaluatorTagRuleFactory.create(team=team_with_users, evaluator=source)

    with patch.object(EvaluatorTagRule.objects, "bulk_create", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            source.copy()

    assert Evaluator.objects.count() == 1
