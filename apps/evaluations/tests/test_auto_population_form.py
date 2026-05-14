import pytest

from apps.evaluations.forms import DatasetAutoPopulationRuleForm
from apps.utils.factories.evaluations import EvaluationDatasetFactory
from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
def test_form_rejects_cross_team_source_experiment():
    dataset = EvaluationDatasetFactory.create(evaluation_mode="session")
    foreign_experiment = ExperimentFactory.create()  # different team

    form = DatasetAutoPopulationRuleForm(
        team=dataset.team,
        dataset=dataset,
        data={
            "source_experiment": foreign_experiment.id,
            "filter_query_string": "",
            "is_enabled": True,
        },
    )
    assert not form.is_valid()
    assert "source_experiment" in form.errors


@pytest.mark.django_db()
def test_form_rejects_invalid_filter_query():
    dataset = EvaluationDatasetFactory.create(evaluation_mode="session")
    experiment = ExperimentFactory.create(team=dataset.team)

    form = DatasetAutoPopulationRuleForm(
        team=dataset.team,
        dataset=dataset,
        data={
            "source_experiment": experiment.id,
            # Half-formed query (missing operator/value pairs):
            "filter_query_string": "filter_0_column=tags",
            "is_enabled": True,
        },
    )
    assert not form.is_valid()
    assert "filter_query_string" in form.errors


@pytest.mark.django_db()
def test_form_accepts_valid_input():
    dataset = EvaluationDatasetFactory.create(evaluation_mode="session")
    experiment = ExperimentFactory.create(team=dataset.team)

    form = DatasetAutoPopulationRuleForm(
        team=dataset.team,
        dataset=dataset,
        data={
            "source_experiment": experiment.id,
            "filter_query_string": "",
            "is_enabled": True,
        },
    )
    assert form.is_valid(), form.errors
    rule = form.save()
    assert rule.team == dataset.team
    assert rule.dataset == dataset
