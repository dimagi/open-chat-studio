import pytest

from apps.events.forms import PipelineStartForm
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory


@pytest.mark.django_db()
def test_pipeline_start_form_excludes_experiments_own_pipeline():
    experiment = ExperimentFactory()
    team = experiment.team
    other_pipeline = PipelineFactory(team=team)

    form = PipelineStartForm(team_id=team.id, experiment_id=experiment.id)

    pipeline_choices = set(form.fields["pipeline_id"].queryset)
    assert experiment.pipeline not in pipeline_choices
    assert other_pipeline in pipeline_choices


@pytest.mark.django_db()
def test_pipeline_start_form_without_experiment_includes_all_pipelines():
    experiment = ExperimentFactory()
    team = experiment.team

    form = PipelineStartForm(team_id=team.id)

    pipeline_choices = set(form.fields["pipeline_id"].queryset)
    assert experiment.pipeline in pipeline_choices


@pytest.mark.django_db()
def test_pipeline_start_form_excludes_only_own_pipeline_for_other_experiment():
    experiment = ExperimentFactory()
    team = experiment.team
    other_experiment = ExperimentFactory(team=team)

    form = PipelineStartForm(team_id=team.id, experiment_id=experiment.id)

    pipeline_choices = set(form.fields["pipeline_id"].queryset)
    assert experiment.pipeline not in pipeline_choices
    assert other_experiment.pipeline in pipeline_choices
