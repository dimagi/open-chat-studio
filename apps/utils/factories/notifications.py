import factory
from django.utils import timezone

from apps.ocs_notifications.models import LevelChoices, Notification, UserNotification
from apps.utils.factories.user import UserFactory


class NotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Notification
        skip_postgeneration_save = True

    title = factory.Faker("sentence", nb_words=4)
    message = factory.Faker("paragraph")
    level = LevelChoices.INFO
    last_event_at = factory.LazyFunction(timezone.now)
    identifier = factory.Sequence(lambda n: f"notification_{n}")


class UserNotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserNotification
        skip_postgeneration_save = True

    notification = factory.SubFactory(NotificationFactory)
    user = factory.SubFactory(UserFactory)
    read = False
    read_at = None
