import factory

from apps.service_providers.models import LlmProvider, MessagingProvider
from apps.utils.factories.team import TeamFactory


class MessagingProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MessagingProvider

    name = factory.Faker("name")
    team = factory.SubFactory(TeamFactory)


class LlmProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LlmProvider

    name = factory.Faker("name")
    team = factory.SubFactory(TeamFactory)
    type = "openai"
    llm_models = ["gtp-4", "gpt-3.5-turbo"]
    config = {"openai_api_key": "123123"}
