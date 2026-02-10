import factory
from django.utils import timezone

from apps.ocs_notifications.models import LevelChoices, Notification, NotificationMute, UserNotification
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


class NotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Notification
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    title = factory.Faker("sentence", nb_words=4)
    message = factory.Faker("paragraph")
    level = LevelChoices.INFO
    last_event_at = factory.LazyFunction(timezone.now)
    identifier = factory.Sequence(lambda n: f"notification_{n}")


class UserNotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserNotification
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    notification = factory.SubFactory(NotificationFactory, team=factory.SelfAttribute("..team"))
    user = factory.SubFactory(UserFactory)
    read = False
    read_at = None


class NotificationMuteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = NotificationMute
        skip_postgeneration_save = True

    team = factory.SubFactory(TeamFactory)
    user = factory.SubFactory(UserFactory)
    notification_identifier = factory.Sequence(lambda n: f"notification_identifier_{n}")
    muted_until = None  # Default to permanent mute
