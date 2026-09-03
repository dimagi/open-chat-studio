import datetime

import factory
import factory.django

from apps.events.models import (
    EventAction,
    EventActionType,
    ScheduledMessage,
    StaticTrigger,
    StaticTriggerType,
    TimeoutTrigger,
)
from apps.events.scheduled_trigger import ScheduledTrigger
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


class ScheduledTriggerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScheduledTrigger

    experiment = factory.SubFactory(ExperimentFactory)
    action = factory.SubFactory(EventActionFactory)
    trigger_date = factory.LazyFunction(lambda: datetime.date.today() + datetime.timedelta(days=1))
    trigger_time = factory.LazyFunction(lambda: datetime.time(9, 0))
    timezone = "UTC"


class ScheduledMessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScheduledMessage

    action = factory.SubFactory(EventActionFactory)
    experiment = factory.SubFactory(ExperimentFactory)
