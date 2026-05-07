import pytest

from apps.evaluations.models import (
    AutoPopulationRunStatus,
    EvaluationDataset,
    EvaluationMode,
)
from apps.evaluations.tasks import _ingest_rule
from apps.utils.factories.evaluations import DatasetAutoPopulationRuleFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_ingest_rule_session_mode_appends_new_sessions():
    team = TeamFactory.create()
    dataset = EvaluationDataset.objects.create(team=team, name="Test Dataset", evaluation_mode=EvaluationMode.SESSION)
    rule = DatasetAutoPopulationRuleFactory.create(team=team, dataset=dataset)
    s1 = ExperimentSessionFactory.create(experiment=rule.source_experiment, team=team)
    s2 = ExperimentSessionFactory.create(experiment=rule.source_experiment, team=team)
    s1.chat.messages.create(message_type="human", content="hi from s1")
    s1.chat.messages.create(message_type="ai", content="hello from s1")
    s2.chat.messages.create(message_type="human", content="hi from s2")
    s2.chat.messages.create(message_type="ai", content="hello from s2")

    appended = _ingest_rule(rule)

    rule.refresh_from_db()
    assert len(appended) == 2
    assert dataset.messages.count() == 2
    assert {m.session_id for m in dataset.messages.all()} == {s1.id, s2.id}
    assert rule.last_run_status == AutoPopulationRunStatus.SUCCESS
    assert rule.last_run_at is not None
    assert rule.consecutive_failure_count == 0


@pytest.mark.django_db()
def test_ingest_rule_no_op_when_no_matches():
    team = TeamFactory.create()
    dataset = EvaluationDataset.objects.create(team=team, name="Test Dataset", evaluation_mode=EvaluationMode.SESSION)
    rule = DatasetAutoPopulationRuleFactory.create(team=team, dataset=dataset)

    appended = _ingest_rule(rule)

    rule.refresh_from_db()
    assert appended == []
    assert dataset.messages.count() == 0
    assert rule.last_run_status == AutoPopulationRunStatus.NO_OP
    assert rule.last_run_at is not None
    assert rule.last_error == ""
    assert rule.consecutive_failure_count == 0
