import factory
import factory.django

from apps.ocs_notifications.models import EventType, EventUser, LevelChoices, NotificationEvent
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


class EventTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventType
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    identifier = factory.Sequence(lambda n: f"event_type_{n}")
    event_data = factory.LazyFunction(dict)
    level = LevelChoices.INFO


class NotificationEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = NotificationEvent
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    event_type = factory.SubFactory(EventTypeFactory, team=factory.SelfAttribute("..team"))
    title = factory.Faker("sentence", nb_words=4)
    message = factory.Faker("paragraph")
    links = None


class EventUserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventUser
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    event_type = factory.SubFactory(EventTypeFactory, team=factory.SelfAttribute("..team"))
    user = factory.SubFactory(UserFactory)
    read = False
    read_at = None
    muted_until = None
