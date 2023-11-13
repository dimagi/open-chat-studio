import factory

from apps.service_providers.models import MessagingProvider
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory


class MessagingProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MessagingProvider

    name = factory.Faker("name")
    team = factory.SubFactory(TeamFactory)
