import factory

from apps.events.models import (
    EventAction,
    EventActionType,
    ScheduledMessage,
    StaticTrigger,
    StaticTriggerType,
    TimeoutTrigger,
)
from apps.utils.factories.experiment import ExperimentFactory


class EventActionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventAction

    action_type = EventActionType.LOG


class StaticTriggerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StaticTrigger

    experiment = factory.SubFactory(ExperimentFactory)
    action = factory.SubFactory(EventActionFactory)
    type = StaticTriggerType.NEW_HUMAN_MESSAGE


class TimeoutTriggerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TimeoutTrigger

    experiment = factory.SubFactory(ExperimentFactory)
    action = factory.SubFactory(EventActionFactory)
    delay = 1
    total_num_triggers = 1


class ScheduledMessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScheduledMessage

    action = factory.SubFactory(EventActionFactory)
    experiment = factory.SubFactory(ExperimentFactory)
