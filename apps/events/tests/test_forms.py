import pytest

from apps.events.forms import PipelineStartForm
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory


@pytest.mark.django_db()
def test_pipeline_start_form_excludes_experiment_linked_pipelines():
    experiment = ExperimentFactory()
    team = experiment.team
    other_experiment = ExperimentFactory(team=team)
    standalone_pipeline = PipelineFactory(team=team)

    form = PipelineStartForm(team_id=team.id)

    pipeline_choices = set(form.fields["pipeline_id"].queryset)
    assert experiment.pipeline not in pipeline_choices
    assert other_experiment.pipeline not in pipeline_choices
    assert standalone_pipeline in pipeline_choices
