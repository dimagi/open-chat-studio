import pytest

from apps.events.forms import EventActionForm, PipelineStartForm
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


@pytest.mark.parametrize(
    ("trigger_type", "action_type", "is_valid"),
    [
        pytest.param("conversation_end", "end_conversation", False, id="end-any-means"),
        pytest.param("conversation_ended_by_event", "end_conversation", False, id="end-by-event"),
        pytest.param("conversation_ended_by_user", "end_conversation", False, id="end-by-user"),
        pytest.param("conversation_ended_by_bot", "end_conversation", False, id="end-by-bot"),
        pytest.param("conversation_ended_via_api", "end_conversation", False, id="end-via-api"),
        pytest.param("conversation_ended_manually", "end_conversation", False, id="end-manually"),
        pytest.param("last_timeout", "end_conversation", True, id="last-timeout"),
        pytest.param("conversation_end", "log", True, id="end-any-means-log"),
        pytest.param("new_human_message", "end_conversation", True, id="human-message"),
    ],
)
def test_event_action_form_end_conversation_on_end_triggers(trigger_type, action_type, is_valid):
    form = EventActionForm(data={"type": trigger_type, "action_type": action_type})

    assert form.is_valid() is is_valid
