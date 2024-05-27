import factory

from apps.events.models import EventAction, EventActionType, ScheduledMessage, StaticTrigger, StaticTriggerType
from apps.utils.factories.experiment import ExperimentFactory


class EventActionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventAction

    action_type = EventActionType.SUMMARIZE


class StaticTriggerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = StaticTrigger

    experiment = factory.SubFactory(ExperimentFactory)
    action = factory.SubFactory(EventActionFactory)
    type = StaticTriggerType.NEW_HUMAN_MESSAGE


class ScheduledMessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScheduledMessage

    action = factory.SubFactory(EventActionFactory)
    experiment = factory.SubFactory(ExperimentFactory)
