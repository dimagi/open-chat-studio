import factory

from apps.chat.models import ScheduledMessage, ScheduledMessageConfig, TimePeriod, TriggerEvent
from apps.utils.factories.experiment import ExperimentFactory


class ScheduledMessageConfigFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScheduledMessageConfig

    experiment = factory.SubFactory(ExperimentFactory)
    team = factory.LazyAttribute(lambda obj: obj.experiment.team)
    name = factory.Faker("name")
    trigger_event = TriggerEvent.PARTICIPANT_JOINED_EXPERIMENT
    recurring = True
    time_period = TimePeriod.DAYS
    frequency = 1
    repetitions = 5
    prompt_text = "Check in with the user"


class ScheduledMessageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ScheduledMessage

    schedule = factory.SubFactory(ScheduledMessageConfigFactory)
    team = factory.LazyAttribute(lambda obj: obj.schedule.team)
