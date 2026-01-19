from unittest import mock

import pytest
from django.test import override_settings

from apps.events.models import EventAction, EventActionType, StaticTrigger, StaticTriggerType
from apps.events.tasks import _get_static_triggers_to_fire, enqueue_static_triggers
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.fixture()
def experiment_session():
    """Create an experiment session for testing."""
    return ExperimentSessionFactory()


@pytest.mark.django_db()
def test_get_static_triggers_to_fire_with_valid_data(experiment_session):
    """Test _get_static_triggers_to_fire with valid session_id and trigger_type."""
    # Create some static triggers for the experiment
    trigger1 = StaticTrigger.objects.create(
        experiment=experiment_session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_START,
    )
    trigger2 = StaticTrigger.objects.create(
        experiment=experiment_session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_START,
    )
    # create an archived trigger
    StaticTrigger.objects.create(
        experiment=experiment_session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_START,
        is_archived=True,
    )
    # Create a trigger with a different type
    StaticTrigger.objects.create(
        experiment=experiment_session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_END,
    )

    # Get triggers for CONVERSATION_START
    trigger_ids = _get_static_triggers_to_fire(experiment_session.id, StaticTriggerType.CONVERSATION_START)

    # Should return the IDs of the two CONVERSATION_START triggers
    assert len(trigger_ids) == 2
    assert set(trigger_ids) == {trigger1.id, trigger2.id}


@pytest.mark.django_db()
def test_get_static_triggers_to_fire_with_different_experiment(experiment_session):
    """Test _get_static_triggers_to_fire with triggers from a different experiment."""
    # Create a trigger for the experiment
    trigger1 = StaticTrigger.objects.create(
        experiment=experiment_session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_START,
    )

    # Create another experiment and a trigger for it
    other_experiment = ExperimentFactory(team=experiment_session.team)
    StaticTrigger.objects.create(
        experiment=other_experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_START,
    )

    # Get triggers for CONVERSATION_START
    trigger_ids = _get_static_triggers_to_fire(experiment_session.id, StaticTriggerType.CONVERSATION_START)

    # Should only return the trigger from the session's experiment
    assert len(trigger_ids) == 1
    assert trigger_ids[0] == trigger1.id


@pytest.mark.django_db()
def test_get_static_triggers_to_fire_with_no_matching_triggers(experiment_session):
    """Test _get_static_triggers_to_fire when no triggers match the criteria."""
    # Create a trigger with a different type
    StaticTrigger.objects.create(
        experiment=experiment_session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_END,
    )

    # Get triggers for CONVERSATION_START (which doesn't exist)
    trigger_ids = _get_static_triggers_to_fire(experiment_session.id, StaticTriggerType.CONVERSATION_START)

    # Should return an empty list
    assert len(trigger_ids) == 0


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@pytest.mark.django_db()
def test_enqueue_static_triggers_calls_fire_for_each_trigger(experiment_session):
    """Test that enqueue_static_triggers calls fire_static_trigger for each trigger."""
    # Create some static triggers
    trigger1 = StaticTrigger.objects.create(
        experiment=experiment_session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_START,
    )
    trigger2 = StaticTrigger.objects.create(
        experiment=experiment_session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_START,
    )

    # Mock the fire_static_trigger.delay method
    with mock.patch("apps.events.tasks.fire_static_trigger.delay") as mock_fire:
        # Call enqueue_static_triggers
        enqueue_static_triggers(experiment_session.id, StaticTriggerType.CONVERSATION_START)

        # Check that fire_static_trigger.delay was called for each trigger
        assert mock_fire.call_count == 2
        mock_fire.assert_any_call(trigger1.id, experiment_session.id)
        mock_fire.assert_any_call(trigger2.id, experiment_session.id)


@pytest.mark.django_db()
def test_get_static_triggers_to_fire_with_versioning(experiment_session):
    experiment_session.experiment.create_new_version(make_default=True)

    # add trigger after versioning
    trigger = StaticTrigger.objects.create(
        experiment=experiment_session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_START,
    )

    trigger_ids = _get_static_triggers_to_fire(experiment_session.id, StaticTriggerType.CONVERSATION_START)
    assert len(trigger_ids) == 0

    experiment_session.experiment.create_new_version(make_default=True)
    trigger_ids = _get_static_triggers_to_fire(experiment_session.id, StaticTriggerType.CONVERSATION_START)
    assert len(trigger_ids) == 1
    assert StaticTrigger.objects.get(pk=trigger_ids[0]).working_version_id == trigger.id


@pytest.mark.django_db()
def test_end_conversation_trigger_also_triggers_generic_end_event(experiment_session):
    conversation_end_trigger = StaticTrigger.objects.create(
        experiment=experiment_session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_END,
    )

    for end_trigger in StaticTriggerType.end_conversation_types():
        trigger = StaticTrigger.objects.create(
            experiment=experiment_session.experiment,
            action=EventAction.objects.create(action_type=EventActionType.LOG),
            type=end_trigger,
        )
        ids = _get_static_triggers_to_fire(experiment_session.id, trigger_type=end_trigger)
        assert len(ids) == 2
        assert trigger.id in ids
        assert conversation_end_trigger.id in ids
