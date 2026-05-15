import pytest
from django.core.exceptions import ValidationError

from apps.evaluations.models import DatasetAutoPopulationRule
from apps.evaluations.notifications import auto_population_rule_disabled_notification
from apps.ocs_notifications.models import NotificationEvent
from apps.utils.factories.evaluations import (
    DatasetAutoPopulationRuleFactory,
    EvaluationDatasetFactory,
)
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_rule_clean_rejects_dataset_team_mismatch():
    other_team = TeamFactory.create()
    dataset = EvaluationDatasetFactory.create(evaluation_mode="session")
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
    dataset = EvaluationDatasetFactory.create(evaluation_mode="session")
    experiment = ExperimentFactory.create()  # different team
    rule = DatasetAutoPopulationRule(
        team=dataset.team,
        dataset=dataset,
        source_experiment=experiment,
    )
    with pytest.raises(ValidationError) as exc_info:
        rule.full_clean()
    assert "source_experiment" in exc_info.value.message_dict


@pytest.mark.django_db()
def test_rule_clean_rejects_message_mode_dataset():
    """Auto-population rules are not supported for message-level datasets."""
    dataset = EvaluationDatasetFactory.create(evaluation_mode="message")
    experiment = ExperimentFactory.create(team=dataset.team)
    rule = DatasetAutoPopulationRule(
        team=dataset.team,
        dataset=dataset,
        source_experiment=experiment,
    )
    with pytest.raises(ValidationError) as exc_info:
        rule.full_clean()
    assert "dataset" in exc_info.value.message_dict
    assert "session-level" in str(exc_info.value.message_dict["dataset"])


@pytest.mark.django_db()
def test_auto_disable_notification_creates_event():
    rule = DatasetAutoPopulationRuleFactory.create()
    auto_population_rule_disabled_notification(rule, reason="three consecutive failures")

    events = NotificationEvent.objects.filter(team=rule.team)
    assert events.count() == 1
    event = events.first()
    assert "auto-population" in event.title.lower()
    assert str(rule.dataset.name) in event.message
