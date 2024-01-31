import factory

from apps.service_providers.models import (
    LlmProvider,
    LlmProviderTypes,
    MessagingProvider,
    VoiceProvider,
    VoiceProviderType,
)
from apps.utils.factories.team import TeamFactory


class MessagingProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MessagingProvider

    name = factory.Faker("name")
    team = factory.SubFactory(TeamFactory)


class LlmProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LlmProvider

    team = factory.SubFactory(TeamFactory)
    type = LlmProviderTypes.openai
    name = factory.Faker("name")
    llm_models = ["gtp-4", "gpt-3.5-turbo"]
    config = {"openai_api_key": "123"}


class VoiceProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VoiceProvider

    team = factory.SubFactory(TeamFactory)
    type = VoiceProviderType.aws
    name = factory.Faker("name")
    config = {"aws_access_key_id": "123", "aws_secret_access_key": "123", "aws_region": "us-east-1"}
