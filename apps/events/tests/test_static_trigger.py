from unittest import mock

import pytest
from django.test import override_settings

from apps.events.models import (
    EventAction,
    EventActionType,
    StaticTrigger,
    StaticTriggerType,
)
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@mock.patch("apps.events.tasks.fire_static_trigger.run")
@pytest.mark.django_db()
def test_end_conversation_fires_event(mock_fire_trigger, session):
    static_trigger = StaticTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_END,
    )
    session.end()

    mock_fire_trigger.assert_called_with(static_trigger.id, session.id)
