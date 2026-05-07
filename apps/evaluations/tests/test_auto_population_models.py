import pytest
from django.core.exceptions import ValidationError

from apps.evaluations.models import DatasetAutoPopulationRule
from apps.utils.factories.evaluations import (
    DatasetAutoPopulationRuleFactory,
    EvaluationDatasetFactory,
)
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_rule_defaults():
    rule = DatasetAutoPopulationRuleFactory.create()
    assert rule.is_enabled is True
    assert rule.filter_query_string == ""
    assert rule.last_run_at is None
    assert rule.last_run_status == ""
    assert rule.last_error == ""
    assert rule.consecutive_failure_count == 0


@pytest.mark.django_db()
def test_rule_clean_rejects_dataset_team_mismatch():
    other_team = TeamFactory.create()
    dataset = EvaluationDatasetFactory.create()
    experiment = ExperimentFactory.create(team=dataset.team)
    rule = DatasetAutoPopulationRule(
        team=other_team,
        dataset=dataset,
        source_experiment=experiment,
    )
    with pytest.raises(ValidationError) as exc_info:
        rule.full_clean()
    assert "dataset" in exc_info.value.message_dict


@pytest.mark.django_db()
def test_rule_clean_rejects_source_experiment_team_mismatch():
    dataset = EvaluationDatasetFactory.create()
    experiment = ExperimentFactory.create()  # different team
    rule = DatasetAutoPopulationRule(
        team=dataset.team,
        dataset=dataset,
        source_experiment=experiment,
    )
    with pytest.raises(ValidationError) as exc_info:
        rule.full_clean()
    assert "source_experiment" in exc_info.value.message_dict
