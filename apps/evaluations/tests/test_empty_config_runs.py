import pytest
from django.urls import reverse

from apps.evaluations.auto_population import _trigger_delta_runs_for_dataset
from apps.evaluations.models import EvaluationRun
from apps.utils.factories.evaluations import EvaluationConfigFactory, EvaluatorFactory


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "url_name",
    [
        pytest.param("evaluations:create_evaluation_run", id="full-run"),
        pytest.param("evaluations:create_evaluation_preview", id="preview"),
    ],
)
def test_triggering_a_config_with_only_archived_evaluators_is_refused(url_name, client, team_with_users):
    """Both trigger views refuse to run a config whose only evaluator is archived."""
    evaluator = EvaluatorFactory.create(team=team_with_users)
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator])
    evaluator.archive()

    client.force_login(team_with_users.members.first())
    response = client.post(reverse(url_name, args=[team_with_users.slug, config.id]))

    assert response.status_code == 400
    assert not EvaluationRun.objects.filter(config=config).exists()


@pytest.mark.django_db()
def test_auto_run_skips_a_config_with_only_archived_evaluators(team_with_users):
    """Positive control: test_ingest_rule_triggers_delta_runs_only_for_opted_in_configs."""
    evaluator = EvaluatorFactory.create(team=team_with_users)
    config = EvaluationConfigFactory.create(team=team_with_users, evaluators=[evaluator], auto_run_on_append=True)
    evaluator.archive()
    message_id = config.dataset.messages.first().id

    _trigger_delta_runs_for_dataset(config.dataset, [message_id])

    assert not EvaluationRun.objects.filter(config=config).exists()
