from datetime import timedelta as _td
from unittest.mock import patch

import pytest

import apps.evaluations.auto_population as eval_tasks
from apps.evaluations.auto_population import _handle_rule_failure, _ingest_rule
from apps.evaluations.models import (
    AutoPopulationRunStatus,
    EvaluationDataset,
    EvaluationMode,
    EvaluationRunType,
)
from apps.ocs_notifications.models import NotificationEvent
from apps.utils.factories.evaluations import DatasetAutoPopulationRuleFactory, EvaluationConfigFactory
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


@pytest.mark.django_db()
def test_ingest_rule_skips_sessions_already_in_dataset():
    team = TeamFactory.create()
    dataset = EvaluationDataset.objects.create(team=team, name="Test Dataset 2", evaluation_mode=EvaluationMode.SESSION)
    rule = DatasetAutoPopulationRuleFactory.create(team=team, dataset=dataset)
    s1 = ExperimentSessionFactory.create(experiment=rule.source_experiment, team=team)
    s1.chat.messages.create(message_type="human", content="x")
    s1.chat.messages.create(message_type="ai", content="y")

    # First tick: ingests s1.
    _ingest_rule(rule)
    assert dataset.messages.count() == 1

    # Second tick: no new sessions, dedup keeps it at 1.
    _ingest_rule(rule)
    assert dataset.messages.count() == 1


@pytest.mark.django_db()
def test_ingest_rule_skips_sessions_older_than_rule_created_at():
    team = TeamFactory.create()
    dataset = EvaluationDataset.objects.create(team=team, name="Test Dataset 3", evaluation_mode=EvaluationMode.SESSION)
    rule = DatasetAutoPopulationRuleFactory.create(team=team, dataset=dataset)
    older = ExperimentSessionFactory.create(experiment=rule.source_experiment, team=team)
    older.created_at = rule.created_at - _td(hours=1)
    older.save(update_fields=["created_at"])

    _ingest_rule(rule)

    assert dataset.messages.count() == 0


@pytest.mark.django_db(transaction=True)
def test_ingest_rule_triggers_delta_runs_only_for_opted_in_configs():
    team = TeamFactory.create()
    dataset = EvaluationDataset.objects.create(team=team, name="Test Dataset 5", evaluation_mode=EvaluationMode.SESSION)
    rule = DatasetAutoPopulationRuleFactory.create(team=team, dataset=dataset)
    opted_in = EvaluationConfigFactory.create(team=team, dataset=dataset, auto_run_on_append=True)
    opted_out = EvaluationConfigFactory.create(team=team, dataset=dataset, auto_run_on_append=False)
    session = ExperimentSessionFactory.create(experiment=rule.source_experiment, team=team)
    session.chat.messages.create(message_type="human", content="x")
    session.chat.messages.create(message_type="ai", content="y")

    with patch("apps.evaluations.tasks.run_evaluation_task.delay"):
        _ingest_rule(rule)

    runs_for_opted_in = opted_in.evaluationrun_set.filter(type=EvaluationRunType.DELTA)
    runs_for_opted_out = opted_out.evaluationrun_set.filter(type=EvaluationRunType.DELTA)
    assert runs_for_opted_in.count() == 1
    assert runs_for_opted_out.count() == 0

    delta_run = runs_for_opted_in.first()
    assert delta_run.scoped_messages.count() == 1


@pytest.mark.django_db()
def test_ingest_rule_no_appends_no_runs():
    team = TeamFactory.create()
    dataset = EvaluationDataset.objects.create(team=team, name="Test Dataset 6", evaluation_mode=EvaluationMode.SESSION)
    rule = DatasetAutoPopulationRuleFactory.create(team=team, dataset=dataset)
    EvaluationConfigFactory.create(team=team, dataset=dataset, auto_run_on_append=True)

    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_delay:
        _ingest_rule(rule)

    mock_delay.assert_not_called()


@pytest.mark.django_db()
def test_handle_rule_failure_increments_counter_and_records_error():
    rule = DatasetAutoPopulationRuleFactory.create()

    _handle_rule_failure(rule, RuntimeError("boom"))

    rule.refresh_from_db()
    assert rule.consecutive_failure_count == 1
    assert rule.last_run_status == AutoPopulationRunStatus.ERROR
    assert "boom" in rule.last_error
    assert rule.is_enabled is True


@pytest.mark.django_db()
def test_third_consecutive_failure_disables_rule_and_emits_notification():
    rule = DatasetAutoPopulationRuleFactory.create(consecutive_failure_count=2)

    _handle_rule_failure(rule, RuntimeError("third strike"))

    rule.refresh_from_db()
    assert rule.consecutive_failure_count == 3
    assert rule.is_enabled is False
    assert NotificationEvent.objects.filter(team=rule.team).count() == 1


@pytest.mark.django_db()
def test_auto_populate_task_skips_disabled_rules_and_isolates_failures(monkeypatch):
    enabled_a = DatasetAutoPopulationRuleFactory.create(is_enabled=True)
    enabled_b = DatasetAutoPopulationRuleFactory.create(is_enabled=True)
    disabled = DatasetAutoPopulationRuleFactory.create(is_enabled=False)

    processed: list[int] = []

    def fake_ingest(rule):
        processed.append(rule.id)
        if rule.id == enabled_a.id:
            raise RuntimeError("rule A blew up")
        return []

    monkeypatch.setattr(eval_tasks, "_ingest_rule", fake_ingest)

    eval_tasks.auto_populate_eval_datasets()

    assert disabled.id not in processed
    assert enabled_a.id in processed
    assert enabled_b.id in processed
    enabled_a.refresh_from_db()
    assert enabled_a.last_run_status == AutoPopulationRunStatus.ERROR
    assert enabled_a.consecutive_failure_count == 1
    # b was processed without error; _ingest_rule is patched so status update
    # comes from _handle_rule_failure only on failure — just verify b was reached
    assert enabled_b.id in processed
